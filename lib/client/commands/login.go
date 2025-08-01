package commands

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"os"
	"time"

	remoteexecution "github.com/bazelbuild/remote-apis/build/bazel/remote/execution/v2"
	"github.com/spf13/cobra"
	"google.golang.org/grpc"
	"google.golang.org/grpc/grpclog"
	"google.golang.org/grpc/metadata"

	apb "github.com/enfabrica/enkit/auth/proto"
	"github.com/enfabrica/enkit/lib/client"
	"github.com/enfabrica/enkit/lib/config/identity"
	"github.com/enfabrica/enkit/lib/kauth"
	"github.com/enfabrica/enkit/lib/kcerts"
	"github.com/enfabrica/enkit/lib/kflags"
	"github.com/enfabrica/enkit/lib/kflags/kcobra"
	"github.com/enfabrica/enkit/lib/retry"
)

type Login struct {
	*cobra.Command
	rng *rand.Rand

	base  *client.BaseFlags
	agent *kcerts.SSHAgentFlags
	retry *retry.Flags

	populator kflags.Populator

	BbclientdAddress string
	Debug            bool
	NoDefault        bool
}

// NewLogin creates a new Login command.
//
// Base is the pointer to a base object, initialized with NewBase.
// rng is a secure random number generator.
//
// When the login command is run, it will:
// - apply the configuration defaults necessary for the domain, using a populator.
// - retrieve an authentication token from the authentication server.
// - save it on disk, optionally as a default identity.
func NewLogin(base *client.BaseFlags, rng *rand.Rand, populator kflags.Populator) *Login {
	login := &Login{
		Command: &cobra.Command{
			Use:     "login",
			Short:   "Retrieve credentials to access the artifact repository",
			Aliases: []string{"auth", "hello", "hi"},
		},
		base:      base,
		agent:     kcerts.SSHAgentDefaultFlags(),
		retry:     retry.DefaultFlags(),
		rng:       rng,
		populator: populator,
	}
	login.Command.RunE = login.Run

	// Authenticating requires the user to visit a website to complete oauth.
	// During that time, the server will either keep the connection hanging (bad)
	// or return an error (thus the client has to retry). If the client only retries
	// 5 times (pre-2025 default), to allow the user 30 mins for authentication, the
	// server must keep connections open for at least 6 minutes - which is high.
	//
	// Set the retry value to a very high number, so we can configure the server with
	// significantly more aggressive timeouts, while allowing enough time for the
	// user to complete the process.
	//
	// A value of 1800 retries, will allow about 30 mins to complete auth assuming
	// the server keeps the connection open for about 1 second.
	login.retry.AtMost = 1800
	// A value of 10 seconds (pre-2025 default) means that the minimum connection
	// open time on the server is also ~10 seconds, otherwise the user would see
	// the client wait for ~10 seconds between attempts to retrieve the token.
	// Set this (the minimum wait time) to a much more aggressive value.
	login.retry.Wait = 1 * time.Second

	login.Flags().StringVar(&login.BbclientdAddress, "bbclientd-address", "localhost:8981", "bbcliend address")
	login.Flags().MarkHidden("bbclientd-address")
	login.Flags().BoolVarP(&login.Debug, "debug", "d", false, "Print extra debugging information. Mostly useful for development")
	login.Flags().BoolVarP(&login.NoDefault, "no-default", "n", false, "Do not mark this identity as the default identity to use")

	klflags := &kcobra.FlagSet{login.Flags()}
	login.agent.Register(klflags, "")
	login.retry.Register(klflags, "login-")

	return login
}

// Adds our auth headers to our requests
func TokenAuthInterceptor(token string) grpc.UnaryClientInterceptor {
	return func(
		ctx context.Context,
		method string,
		req interface{},
		reply interface{},
		cc *grpc.ClientConn,
		invoker grpc.UnaryInvoker,
		opts ...grpc.CallOption,
	) error {
		// TODO(isaac): This is a little non-standard - perhaps we should make these
		// constants somewhere in the enkit codebase?
		md := metadata.Pairs("cookie", "Creds="+token)
		ctxWithToken := metadata.NewOutgoingContext(ctx, md)

		return invoker(ctxWithToken, method, req, reply, cc, opts...)
	}
}

// AuthenticateBbclientd auths our CAS mounts
//
// bb_clientd mounts CAS as a fuse filesystem.
// It also runs a grpc enpoint that acts as a
// proxy for our build cluster backend.
//
// It authenticates the fuse filesystem by
// intercepting credentials that come from requests
// from the RBE client (bazel). Those credentials
// are reused when a user wants to read a file from CAS
// (testslogs from failed builds, etc..).
//
// The issue with this auth setup is that users must do
// an initial bazel invocation to "seed" the credentials
// before they're able to use the CAS mounts. This is a poor
// user experience.
//
// To get around this we're sending a dummy rpc request through
// the proxy at `enkit login` so that bbclientd is automatically
// authenticated on user login for the day.
//
// A less hacky approach would be to add proper credentials helper support
// for bb_clientd so auth happens when the daemon starts up. Upstream has already
// indicated they'd be happy to accept this contribution. If we're able to land that
// feature (or someone else does) then this code can be removed.
//
// The ticket for cred helper support in bb_clientd: ENGPROD-355
func (l *Login) AuthenticateBbclientd(address string, token string, debug bool) {
	if debug {
		grpclog.SetLoggerV2(grpclog.NewLoggerV2(os.Stdout, os.Stderr, os.Stderr))
		grpc.EnableTracing = true
	}
	var conn *grpc.ClientConn
	var err error

	conn, err = grpc.Dial(address, grpc.WithInsecure(), grpc.WithUnaryInterceptor(TokenAuthInterceptor(token)), grpc.WithTimeout(2*time.Second))
	if err != nil {
		if debug {
			log.Fatal("fail to dial: %w", err)
		}

		// continuing if we fail here will cause a crash.
		return
	}

	// The FindMissingBlobs RPC is used by the client to ask the server which blobs it is missing
	// so it can upload them. We just send an abritrary digest (to which the server should respond
	// that it doesn't have it). Note that we don't really care about the response here unless it's an
	// error.
	digests := []*remoteexecution.Digest{
		{Hash: "013ad2661e3240ec6e0c8f79eb14944f599e04aeffa78d90873a6d679297746c", SizeBytes: 22733},
	}

	client := remoteexecution.NewContentAddressableStorageClient(conn)
	_, err = client.FindMissingBlobs(context.Background(), &remoteexecution.FindMissingBlobsRequest{BlobDigests: digests})
	if err != nil {
		l.base.Log.Errorf("Failed to auth bbclientd daemon: %v", err)
	}
}

func (l *Login) Run(cmd *cobra.Command, args []string) error {
	if len(args) > 1 {
		return kflags.NewUsageErrorf("use as 'astore login username@domain.com' or just '@domain.com' - exactly one argument")
	}

	ids, err := l.base.IdentityStore()
	if err != nil {
		return fmt.Errorf("could not open identity store - %w", err)
	}

	argname := l.base.Identity()
	if len(args) >= 1 {
		argname = args[0]
	} else if argname == "" {
		argname, _, _ = ids.Load("")
	}

	username, domain := identity.SplitUsername(argname, l.base.DefaultDomain)
	if domain == "" {
		return kflags.NewUsageErrorf("Please specify your 'username@domain.com' as first argument, '... login myname@mydomain.com'")
	}

	// Once we know the domain of the user, we can load the options associated with that domain.
	// Note that here we have no token yet, as the authentication process has not been started yet.
	if l.populator != nil {
		if err := l.base.UpdateFlagDefaults(l.populator, domain); err != nil {
			l.base.Log.Infof("updating default flags failed: %s", err)
		}
	}

	conn, err := l.base.Connect()
	if err != nil {
		return err
	}
	repeater := retry.New(retry.FromFlags(l.retry), retry.WithRng(l.rng))
	enCreds, err := kauth.PerformLogin(apb.NewAuthClient(conn), l.base.Log, repeater, l.rng, username, domain)
	if err != nil {
		return err
	}
	l.base.Log.Infof("Storing credentials in SSH agent...")
	if err := kauth.SaveCredentials(enCreds, l.base.Local, kcerts.WithLogging(l.base.Log), kcerts.WithFlags(l.agent)); err != nil {
		l.base.Log.Errorf("Failed to save credentials: %v", err)
		return err
	}

	// TODO(adam): delete below when we are comfortable migrating from the token to pure ssh certificates
	l.base.Log.Infof("Storing identity in $HOME config...")
	userid := identity.Join(username, domain)
	err = ids.Save(userid, enCreds.Token)
	if err != nil {
		return fmt.Errorf("could not store identity: %w", err)
	}
	if l.NoDefault == false {
		err = ids.SetDefault(userid)
		if err != nil {
			return fmt.Errorf("could not mark identity as default: %w", err)
		}
	}
	l.base.Log.Infof("Stored identity in $HOME config")

	l.base.Log.Infof("Performing buildbarn request so that bb_clientd picks up credentials...")
	l.AuthenticateBbclientd(l.BbclientdAddress, enCreds.Token, l.Debug)

	return nil
}
