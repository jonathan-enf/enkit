#!/usr/bin/python3
"""geet: yeet git, get geet!

A port of gee to python3, using only modules that are part of the Python
Standard Library https://docs.python.org/3/library/

See "USAGE" below for details.
"""

import argparse
import collections.abc
import os
import subprocess
import sys

VERSION="0.2.29"

USAGE=f"""
. __ _  ___  ___ _____
 / _` |/ _ \/ _ \_   _| git
| (_| |  __/  __/ | |   enabled
 \__, |\___|\___| |_|   enfabrication
 |___/                  tool

geet version: {VERSION}

geet is a wrapper around the "git" and "gh-cli" tools.

geet implements one standard and well-supported "best practices" workflow, out
of the many possible ways of using git and gh-cli.

geet is a repository of learned wisdom about using git, which largely means an
iterative process of breaking things with git, and then patching geet to
prevent a similar breakage in the future.

geet is also an instructional tool: by showing each command as it executes,
geet helps users learn git.

## Features:

Uses the "worktree" feature so that:

* every branch is always visible in its own directory.
* switching branches is accomplished by changing directory.
* it's harder to accidentally save changes to the wrong branch.
* users can have uncommitted changes pending in more than one branch.

All branch directories are named ~/geet/<REPO>/<BRANCH>.

All local commits are automatically backed up to github.

Tracks "parentage" (which branch is derived from which).

Sets up and enforces use of ssh for all interactions with github.

Supports multi-homed development (user can do work on various hosts without
NFS-mounted home directories).

## An example of simple use:

1. Run "geet init" to clone and check out the enfabrica/internal repo.  This
   only needs to be done once per home directory.

2. "cd ~/geet/internal/main" to start in the main branch.

3. Make a feature branch: "geet make_branch my_feature"
   Then: "cd $(geet gcd my_feature)"

4. Make some changes, and call "geet commit" whenever needed to checkpoint
   your work.

5. Call "geet update" to pull new changes from upstream.

6. When ready to send your change out for review:

    geet fix  # runs all automatic code formatters
    geet commit -a -m "ran geet fix"
    geet make_pr  # creates a pull request.

7. You can continue to make updates to your branch, and update your
   PR by running "geet commit".

8. When approved, run "geet submit_pr" to merge your change.

## An example of more complex use:

You can continue to develop a second feature while the first feature is out for
review.

1. Make a branch of a branch:

     cd $(geet gcd my_feature)
     geet mkbr my_feature2

2. Do work in the child branch:

     cd $(geet gcd my_feature2)

3. Recursively update a chain of branches:

     geet rupdate

"""

# Philosophy:
#
#    If I have to look it up on stack overflow, it should get encoded in gee.
#    Automatic but not automagic.  No surprises.  Show your work.  Never change
#    the system in a way the user doesn't expect.  When in doubt, ask.
#
#    Do what I mean: I'm lysdexic, so a lot of the commands have reverse order
#    aliases.
#
# Branches:
#    upstream: the original repo we have forked (enfabrica/$REPO)
#              we issue pull requests to this repo, and we integrate
#              changes from here.
#    origin:   the user's forked repo ($GHUSER/$REPO)
#              we pull and push to this repo.
#    upstream/main: top of tree
#    origin/main: user's top-of-tree with no local changes,
#        updated periodically from upstream/main.
#    main: local repo top of tree, no local changes,
#        updated periodically from upstream/main.
#    $feature: fork of main (or other $feature), contains
#        local changes.  May have 0 or 1 PRs associated with it.
#    origin/$feature: github backup of $feature branch.
#
# Updates flow in one direction through these branches:
#
#   upstream/main -> main -> origin/main -> $feature -> origin/$feature
#
# The user only commits changes to $feature.
#
# Changes then migrate from origin/$feature back to upstream/main when a PR is
# approved and merged.
#
# Note: We are transitioning from "master" to "main."  Gee always tries to find
# a "main" remote branch first, but falls back to "master" if "main" does not
# exist.
#
# TODO(jonathan): Bash implementation is a prototype.  rewrite in golang?
# TODO(jonathan): "git push -u" option is going to change soon.


def _fix_pwd():
  # Make sure we're in a real directory.
  #
  # Sometimes funny things happen with git and directories:
  #  - git rebase can re-create a directory, changing the inode and leaving pwd
  #    invalid.
  #  - removing a worktree can leave the pwd in an invalid directory.
  #
  # Let's make sure we're in a directory that exists:
  cwd = os.getcwd()
  cwd_parts = cwd.split("/")
  real_cwd = "/"
  for part in cwd_parts:
      if not part:
          continue
      attempt = os.path.join(real_cwd, part)
      if not os.path.isdir(attempt):
          break
      real_cwd = attempt
  os.chdir(real_cwd)
_fix_pwd


class Gee(object):
    """Gee is a singleton that holds the state of the current gee context.

    It provides a number of capabilities (holding gee configuration and
    metadata, running git and gh commands, etc.).

    Everything is lazy-initialized, so we only check if a specific capability
    is configured correctly when we attempt to use it (for example, "parents"
    metadata is loaded when used).
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GeeContext, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        super().__init__(self)
        self.alias_map = {}
        self.rev_alias_map = {}
        self.cmd_doc_map = {}
        self.parents = None
        self.mergebases = None
        self.gee_on_astore="test/gee"
        self.git="/usr/bin/git"
        self.gh="/usr/bin/gh"
        self.jq="/usr/bin/jq"
        self.sshkeyfiles=("${HOME}/.ssh/id_ed25519" "${HOME}/.ssh/gee_github_ed25519")
        self.sshkeyfile="${SSHKEYFILES[0]}"  # default
        # Backwards compatibility: Use first keyfile that exists.
        for f in self.sshkeyfiles
        for f in "${SSHKEYFILES[@]}"; do
          if [[ -f "${f}" ]]; then
            SSHKEYFILE="${f}"
            break
          fi
        done
        self.enkit=/opt/enfabrica/bin/enkit
        self.git_at_github="org-64667743@github.com"
        self.newline=$'\n'
        self.clone_depth_months=3  # months of history to fetch
        self.yesyesyes="${YESYESYES:-0}"  # for testing: disables all interactivity.
        self.ghuser="${GHUSER:-}"  # can be set by _startup_checks
        self.verbose="${VERBOSE:-1}"
        self.dryrun="${DRYRUN:-0}"
        self.upstream="${UPSTREAM:-enfabrica}"
        self.testmode="${TESTMODE:-0}"
        self.main="" # Unknown, call _set_main to set.
        self.repo="${REPO:-}"
        self.pager="${PAGER:-less}"
        self.parents_file_is_loaded=0
        self.pwd_cmd="$(command -v pwd)"  # dev: /usr/bin/pwd, fpga-dev: /bin/pwd  :-(
        self.flags=()  # for _parse_options(), below
        self.args_positional=()  # for _parse_options(), below

    def GetSubparsers(self):
        return self.subparsers

    def RegisterCommand(self, command: str, aliases: list[str], short_help: str, docstr: str):
        for cmd in [command] + aliases:
            self.alias_map[cmd] = command
        self.rev_alias_map[command] = aliases
        self.cmd_doc_map[command] = docstr

    def Invoke(self, args):
        cmd = args[0]
        if cmd not in globals():
            self.Usage()
            return 1
        return globals()[cmd](self, args[1:])

# Globals:

# Make sure we're in a directory that exists:
while ! "${PWD_CMD}" >/dev/null; do
  cd ..
done

if [[ -z "${REPO}" ]]; then
  # Examine the directory to see if we're in a repo already.
  # Try the old directory layout:
  if [[ "${PWD}" =~ ^${HOME}/([a-z0-9_-]+)/branches ]]; then
    REPO="${BASH_REMATCH[1]}"
  fi
  # Try the new directory layout:
  if [[ "${PWD}" =~ ^${HOME}/gee/([a-z0-9_-]+) ]]; then
    REPO="${BASH_REMATCH[1]}"
  fi
  # Try the test directory layout:
  if [[ "${PWD}" =~ ^${HOME}/testgee/([a-z0-9_-]+) ]]; then
    TESTMODE=1
    REPO="${BASH_REMATCH[1]}"
  fi
fi

# If all else fails, default to internal
if [[ -z "${REPO}" ]]; then
  REPO=internal
fi

GEE_DIR="${HOME}/gee"
REPO_DIR="${GEE_DIR}/${REPO}/"

if (( "${TESTMODE}" )); then
  UPSTREAM="enfabrica"
  REPO="github-playground"
  GEE_DIR="${HOME}/testgee"
  REPO_DIR="${GEE_DIR}/${REPO}/"
fi

def safe_tput():
  if [[ -z "${TERM}" ]] || [[ "${TERM}" == "none" ]]; then
    return
  fi
  tput "$@"
}

# colors library
_COLOR_RST="$(safe_tput sgr0)"
_COLOR_CMD="$(safe_tput bold; safe_tput setaf 12; safe_tput rev)"
_COLOR_BANNER="$(safe_tput bold; safe_tput setab 21; safe_tput setaf 15)"
#_COLOR_CMD="$(safe_tput bold; safe_tput setaf 14; safe_tput setab 16)"
_COLOR_DBG="$(safe_tput setaf 2)"
_COLOR_DIE="$(safe_tput bold; safe_tput setaf 15; safe_tput setab 3)"
_COLOR_WARN="$(safe_tput setaf 11)"
_COLOR_INFO="$(safe_tput setaf 1)"

##########################################################################
# utility functions
#
# (the good stuff, the commands, are in the next section below.)
##########################################################################

def _parse_options():
  # _parse_options <optstring> <args...>
  #
  # This function populates the global FLAGS associative array with all
  # of the set flags.  Any remaining positional arguments are left behind
  # in the ARGS_POSITIONAL array
  export FLAGS=()
  export ARGS_POSITIONAL=()
  local optstring="$1"; shift
  local arg
  OPTERR=0  # enable silent error reporting
  while [[ $# -gt 0 ]]; do
    unset OPTARG
    unset OPTIND
    while getopts "${optstring}" arg; do
      if [[ "${arg}" == "?" ]]; then
        local x
        x=$((OPTIND-1))
        _fatal "Bad command flag: ${!x}"
      fi
      FLAGS["${arg}"]="${OPTARG:-1}"
    done
    shift $((OPTIND-1))
    if [[ $# -gt 0 ]]; then
      ARGS_POSITIONAL+=( "$1" )
      shift
    fi
  done
  return 0
}

def _contains_element():
  # Returns true if the first argument is present in one of the subsequent
  # arguements.
  local MATCH="$1"
  shift
  local E
  for E in "$@"; do
    if [[ "$E" == "${MATCH}" ]]; then
      return 0
    fi
  done
  return 1
}

def _egrep_array():
  # Usage: _egrep_array <output> <regex> <elements...>
  #
  # Filters a list of elements by a regex, and stores an array of those
  # filtered elements into the specified "output" variable.
  local OUTPUT="$1"
  local REGEX="$2"
  unset "${OUTPUT}"
  declare -a "${OUTPUT}"
  readarray -t "${OUTPUT}" < <( printf "%s\n" "$@" | grep -E "${REGEX}" | cat )
}

def _set_alias_if_missing():
  # Creates a git alias, only if one does not already exist.
  local ALIAS="$1"
  local DEFN="$2"
  if ! "${GIT}" config --get "alias.${ALIAS}" >/dev/null; then
    _git config --global "alias.${ALIAS}" "${DEFN}"
  fi
}

def _set_main_by_asking_github():
  _check_ssh
  local UPSTREAM_URL
  UPSTREAM_URL="${GIT_AT_GITHUB}:${UPSTREAM}/${REPO}.git"
  MAIN="$(
    "${GIT}" remote show "${UPSTREAM_URL}" \
      | awk '/HEAD branch/ {print $NF}'
  )"
  if [[ -z "${MAIN}" ]]; then
    _die "Can't identify default branch for ${UPSTREAM_URL}."
  fi
}

def _set_main():
  # Sets the ${MAIN} global variable to be the name of the main branch of the
  # current repository.  Usually "main" or "master," but not always.

  # Use a cached result if available:
  if [[ -n "${MAIN}" ]]; then return; fi

  # If a master or main branch exist on our local file system, assume:
  if [[ -d "${REPO_DIR}/master" ]]; then
    MAIN=master
    return
  elif [[ -d "${REPO_DIR}/main" ]]; then
    MAIN=main
    return
  fi

  # Ok, let's ask github:
  _set_main_by_asking_github
}

def _set_ghuser():
  # Attempts to ensure that the GHUSER environment variable is set correctly.
  # If GHUSER is unset, _set_ghuser first tries to get the username from
  # github.  Failing that, the default username of $(whoami)-enf is set.
  if [[ -n "${GHUSER}" ]]; then
    return 0
  fi

  # try to get ghuser from ssh interface:
  local OUTPUT
  set +e
  OUTPUT="$(ssh -T "${GIT_AT_GITHUB}" 2>&1)"
  set -e
  if [[ "${OUTPUT}" =~ ^Hi\ ([a-zA-Z0-9_-]+) ]]; then
    GHUSER="${BASH_REMATCH[1]}"
    return 0
  fi

  # let's just ask the user
  _warn "Can't automatically determine your github username."
  if [[ -z "${USER}" ]]; then
    USER="$(whoami)"
  fi
  if [[ -n "${USER}" ]]; then
    GHUSER="${USER}-enf"
  fi
  if (( ! YESYESYES )); then
    read -r -i "${GHUSER}" -p "What is your github username?  " -e GHUSER
  fi

  if [[ -z "${GHUSER}" ]]; then
    _fatal "Cannot proceed without a github username."
  fi
  if _confirm_default_no \
    "Is it okay to add \"export GHUSER=${GHUSER}\" to your .bashrc file? (y/N)  "
  then
    printf "\nexport GHUSER=%q\n" "${GHUSER}" >> ~/.bashrc
  fi
}


def _check_ssh_agent():
  # Check that the ssh-agent is loaded and reachable.
  if [[ -z "${SSH_AGENT_PID}" ]]; then
    _warn "SSH_AGENT_PID is not set."
    _info "Consider adding \"eval \`enkit agent print\`\" to your .bashrc"
    if _confirm_default_no \
      "Would you like gee to append this line to your .bashrc file now? (y/N)  "
    then
      printf "eval \`enkit agent print\`\n" >> ~/.bashrc
    fi
    eval "$(enkit agent print)"
    if [[ -z "${SSH_AGENT_PID}" ]]; then
      _fatal "Something is wrong with enkit's ssh agent."
    fi
  fi
  if ! ssh-add -l > /dev/null; then
    local RC=$?
    if (( RC == 2 )); then
      _fatal "Unable to communicate with enkit's ssh agent."
    fi
  fi
}

def _check_enkit_cert():
  # Check if we have a valid enkit authentication token.
  local COUNT
  COUNT="$("${ENKIT}" agent print | wc -l)"
  if (( COUNT == 0 )); then
    _warn "enkit certificate is expired."
    _info "Please authenticate:"
    "${ENKIT}" login
    COUNT="$("${ENKIT}" agent print | wc -l)"
    if (( COUNT == 0 )); then
      _fatal "No enkit certificate, aborting."
    fi
  fi
  COUNT="$(ssh-add -l | wc -l)"
  if (( COUNT == 0 )); then
    _fatal "enkit's certificate isn't showing up in ssh-agent."
  fi
}

def _check_ssh():
  # Troubleshoot ssh connection to github.

  # First check if we have an ssh-agent running:
  _check_ssh_agent

  # Check that the enkit certification is available
  _check_enkit_cert

  # Check if we can connect via ssh to github.
  # Takes 0.7 seconds.  Use only as-needed.
  local OUTPUT
  set +e
  OUTPUT="$(ssh -T "${GIT_AT_GITHUB}" 2>&1)"
  set -e
  if [[ "${OUTPUT}" =~ ^Hi\ ([a-zA-Z0-9_-]+) ]]; then
    GHUSER="${BASH_REMATCH[1]}"
    return 0
  fi
  _warn "Could not authenticate to github using ssh."
  _info "ssh -T \"${GIT_AT_GITHUB}\" got:"
  _info "  ${OUTPUT}"

  if [[ -f "${SSHKEYFILE}" ]]; then
    _info "Perhaps you need to run: ssh-add ${SSHKEYFILE}"
    _cmd ssh-add "${SSHKEYFILE}"
    _info "Trying again..."
    set +e
    OUTPUT="$(ssh -T ${GIT_AT_GITHUB} 2>&1)"
    set -e
    if [[ "${OUTPUT}" =~ ^Hi\ ([a-zA-Z0-9_-]+) ]]; then
      GHUSER="${BASH_REMATCH[1]}"
      return 0
    fi
    _warn "Still couldn't authenticate to github using ssh."
    _info "ssh -T ${GIT_AT_GITHUB} got:"
    _info "  ${OUTPUT}"
  fi
  return 1
}

def _check_gh_auth():
  # Check that the gh-cli tool has a valid access token for
  # communicating with github.
  local OUTPUT RC

  set +e
  OUTPUT="$("${GH}" auth status 2>&1)";
  RC=$?
  set -e
  if (( RC != 0 )); then
    _info "Could not authenticate to github.  Got:" "${OUTPUT}"
    _info "Let's try refreshing your access token:"
    _cmd "${GH}" auth login
    if ! "${GH}" auth status; then
      _fatal "Still can't authenticate to github."
    fi
  fi
}


def _check_cwd():
  # Check that we're in a directory beneath ~/gee.
  local DIR
  DIR="$("${PWD_CMD}")"
  if ! [[ "$DIR" =~ ^"${GEE_DIR}"/[a-zA-Z0-9_-]+ ]]; then
    echo "${DIR}"
    _fatal "This command must be run from with a branch directory beneath ~/gee."
  fi
}

def _get_ghuser_via_ssh():
  # Set GHUSER or die.
  if ! _check_ssh; then
    _fatal "Could not determine github username."
  fi
}

def _startup_checks():
  # Ensure that (most) gee commands are run from within the gee repository,
  # otherwise strange things might happen (if the user's home directory is a
  # git repository, for instance).
  local GITDIR
  GITDIR="$(readlink -f "$("${GIT}" rev-parse --git-common-dir)")"
  if [[ "${GITDIR}" != "${GEE_DIR}"/*/.git ]]; then
    _warn "Error: refusing to run this gee command outside of the gee" \
          "repository."
    if [[ -d "${GEE_DIR}" ]]; then
      _info "Use \"gcd\" to switch to a gee branch and try again."
    else
      _info "No gee repository found, run \"gee init\" to create one."
    fi
    exit 1
  fi

  # Troubleshoot our environment before running (most) commands.
  if [[ ! -x "${GIT}" ]]; then
    _fatal "${GIT} is not installed."
  fi
  if [[ ! -x "${GH}" ]]; then
    _fatal "${GH} is not installed."
  fi

  # If GHUSER is unset, find username via ssh to github.
  if [[ -z "${GHUSER}" ]]; then
    _set_ghuser
  fi

  local GIT_EMAIL
  GIT_EMAIL="$("${GIT}" config --get user.email | cat)"
  if [[ -z "${GIT_EMAIL}" ]]; then
    _warn "git user.email setting is empty."
    GIT_EMAIL="${USER}@enfabrica.net"
    if (( ! YESYESYES )); then
      read -e -r -i "${GIT_EMAIL}" -p \
          "What email address should git use for you?   " \
          GIT_EMAIL
    fi
    if [[ -z "${GIT_EMAIL}" ]]; then
      _fatal "git needs user.email to be set."
    fi
    _git config --global user.email "${GIT_EMAIL}"
  fi

  local GIT_NAME
  GIT_NAME="$("${GIT}" config --get user.name | cat)"
  if [[ -z "${GIT_NAME}" ]]; then
    _warn "git user.name setting is empty."
    GIT_NAME="${USER}"
    if (( ! YESYESYES )); then
      read -e -r -i "${GIT_NAME}" -p \
        "What name should git use for you?   " \
        GIT_NAME
    fi
    if [[ -z "${GIT_NAME}" ]]; then
      _fatal "git needs user.name to be set."
    fi
    _git config --global user.name "${GIT_NAME}"
  fi

  # check ssh agent
  local RC
  set +e
  ssh-add -l >/dev/null
  RC=$?
  set -e
  if (( RC == 0 )); then
    return 0
  elif (( RC == 1 )); then
    if [[ -f "${SSHKEYFILE}" ]]; then
      _cmd ssh-add "${SSHKEYFILE}"
    fi
  elif (( RC == 2 )); then
    _warn "Could not connect to ssh-agent."
    _info "Consider adding \"eval \`enkit agent print\`\" to your .bashrc"
    if _confirm_default_no \
      "Would you like gee to append this line to your .bashrc file now? (y/N)  "
    then
      printf "eval \`enkit agent print\`\n" >> ~/.bashrc
    fi
    eval "$(enkit agent print)"
  fi

  set +e
  ssh-add -l >/dev/null
  RC=$?
  set -e
  if (( RC == 1 )); then
    _fatal "ssh-agent doesn't report any keys.  Please \"enkit login\" and try again."
  elif (( RC == 2 )); then
    _fatal "Persistent failure connecting to ssh-agent."
  elif (( RC != 0 )); then
    _fatal "Unknown error from ssh-add command: RC=${RC}"
  fi
}

def _ssh_enroll():
  # Enroll the user for ssh access to github by creating and uploading a keyfile.
  if [[ -z "${SSH_AUTH_SOCK}" ]]; then
    _warn "No ssh-agent is running."
    _info "Please add \"eval \`enkit agent print\`\" to your .bashrc"
    if _confirm_default_no \
      "Would you like gee to append this line to your .bashrc file now? (y/N)  "
    then
      printf "eval \`enkit agent print\`\n" >> ~/.bashrc
    fi
    eval "$(enkit agent print)"
  fi
  if [[ -z "${SSH_AUTH_SOCK}" ]]; then
    _fatal "gee requires ssh-agent to be running.  Fix and retry."
  fi

  if [[ ! -f "${SSHKEYFILE}" ]]; then
    _cmd ssh-keygen -f "${SSHKEYFILE}" -t ed25519 -C "${USER}@enfabrica.net"
  else
    _info "Reusing existing ${SSHKEYFILE}"
  fi
  if [[ ! -f "${SSHKEYFILE}" ]]; then
    _fatal "Key file ${SSHKEYFILE} was not created."
  fi

  local COUNT
  COUNT="$("${ENKIT}" agent list | wc -l)"
  if (( COUNT == 0 )); then
    _warn "No enkit certificates are present."
    _info "Let's try authenticating:"
    "${ENKIT}" auth
    COUNT="$("${ENKIT}" agent list | wc -l)"
    if (( COUNT == 0 )); then
      _die "Still could not find any enkit certificates."
    fi
  fi

  local COUNT
  COUNT="$("${ENKIT}" agent list | wc -l)"
  if (( COUNT == 0 )); then
    _warn "No enkit certificates are present."
    _info "Let's try authenticating:"
    "${ENKIT}" auth
    COUNT="$("${ENKIT}" agent list | wc -l)"
    if (( COUNT == 0 )); then
      _die "Still could not find any enkit certificates."
    fi
  fi

  # TODO(jonathan): Is this necessary?
  if [[ "${SSHKEYFILE}" != "${SSHKEYFILES[0]}" ]]; then
    cat <<EOT >> ~/.ssh/config
# gee: block start
Host *.github.com
  IdentityFile ${SSHKEYFILE}
# gee: block stop
EOT
  fi

  _cmd ssh-add "${SSHKEYFILE}"
  _gh config set git_protocol ssh

  _info "Checking your github access..."
  if ! _gh auth status; then
    _info "Let's try authenticating with the gh tool:"
    # Override BROWSER because xdg-open opens links2 on some systems, and
    # links2 doesn't support github.
    BROWSER="bash -c \"echo Open this URL: \$*\" --" \
      _gh auth login
    # The user might have to open their web browser at this point:
    if (( ! YESYESYES )); then
      read -n1 -rsp $'Press any key to continue, or Control-C to exit.\n'
    fi
  fi

  if ! _gh ssh-key add "${SSHKEYFILE}.pub" --title "gee-created-key"; then
    _warn "Could not add your key to github (maybe it's already there?)."
  fi

  _gh ssh-key list

  if ! _check_ssh; then
    _fatal "Something still wrong: can't authenticate to github via ssh."
  fi
}

def _register_help():
  # Registers help text about a gee command.
  #
  # Usage: _register_help <command> <shorthelp> <aliases...>
  #
  # The "long help" for this command must be presented to this
  # function as stdin.
  local COMMAND SHORT LONG ALIAS
  COMMAND="$1"
  shift
  SHORT="$1"
  shift
  LONG="$(</dev/stdin)"
  HELP+=( "${COMMAND}: ${SHORT}" )
  local -a ALIASES
  ALIASES=("$@")
  if (( "${#ALIASES[@]}" > 0 )); then
    LONG="Aliases: ${ALIASES[*]}${NEWLINE}${NEWLINE}${LONG}"
  fi
  LONGHELP+=(["${COMMAND}"]="${LONG}")
  for ALIAS in "${ALIASES[@]}"; do
    LONGHELP+=(["${ALIAS}"]="${LONG}")
  done
}

def _get_parent_branch():
  # Return the name of the branch that is the parent of this
  # branch.
  local BRANCH
  BRANCH="$1"
  if [[ -z "${BRANCH}" ]]; then
    BRANCH="$(_get_current_branch)"
  fi

  _read_parents_file
  # BRANCH should be in the parents file.  Let's check there first:
  if [[ -v PARENTS["${BRANCH}"] ]]; then
    echo "${PARENTS[$BRANCH]}"
    return
  fi

  _set_main
  if [[ "${BRANCH}" == "${MAIN}" ]]; then
    # Parent of $MAIN is always upstream/$MAIN.
    PARENTS["${BRANCH}"]="upstream/${MAIN}"
    echo "upstream/${MAIN}"
    return
  fi

  _warn "Strangely, ${BRANCH} was missing from ${REPO_DIR}/.gee/parents."

  # The safest thing to do is to just set parent to $MAIN and move on.
  PARENTS["${BRANCH}"]="${MAIN}"
  echo "${MAIN}"
}

def _gee_get_all_children_of():
  local PARENT="$1"
  local -a QUEUE=( "${PARENT}" )
  local -A KIDSMAP=()
  _read_parents_file  # populate PARENTS associative array
  while [[ "${#QUEUE[@]}" -ne 0 ]]; do
    PARENT="${QUEUE[0]}"
    QUEUE=("${QUEUE[@]:1}")  # shift QUEUE
    for CHILD in "${!PARENTS[@]}"; do
      if [[ "${PARENTS["${CHILD}"]}" == "${PARENT}" ]]; then
        if ! [[ -v "KIDSMAP[${CHILD}]" ]]; then
          KIDSMAP["${CHILD}"]=1
          QUEUE+=("${CHILD}")
        fi
      fi
    done
  done
  printf "%q\n" "${!KIDSMAP[@]}" | sort
}

def _update_branch_to_worktree():
  # Initialize the global BRANCH_TO_WORKTREE associative array with
  # a mapping of branch names onto worktree paths.
  BRANCH_TO_WORKTREE=()  # global
  local LINE WT BR
  _set_main
  while IFS="" read -r LINE; do
    if [[ "${LINE}" =~ ^worktree\ (.+) ]]; then
      WT="${BASH_REMATCH[1]}"
    elif [[ "${LINE}" =~ ^branch\ refs/heads/(.+) ]]; then
      BR="${BASH_REMATCH[1]}"
    elif [[ "${LINE}" == "" ]]; then
      if [[ -n "${BR}" ]]; then
        BRANCH_TO_WORKTREE["${BR}"]="${WT}"
      fi
    fi
  done < <(cd "${GEE_DIR}/${REPO}/${MAIN}"; "${GIT}" worktree list --porcelain ) \
    || /bin/true
}

def _get_branch_rootdir():
  # Return the root directory of a git branch
  local BRANCH_NAME BRDIR
  BRANCH_NAME="$1"
  if [[ -z "${BRANCH_NAME}" ]]; then
	  BRANCH_NAME="$(_get_current_branch)"
  fi
  _update_branch_to_worktree
  BRDIR="${BRANCH_TO_WORKTREE["${BRANCH_NAME}"]}"
  if [[ -z "${BRDIR}" ]]; then
    _die "_get_branch_rootdir failed for ${BRANCH_NAME}"
  fi
  echo "${BRDIR}"
}

def _choose_one():
  local PROMPT="$1"
  local OPTIONS="$2"
  local DEFAULT="$3"
  declare -g RESP="${DEFAULT}"
  if (( YESYESYES )); then
    echo "${PROMPT}: default to ${DEFAULT}"
    return 0
  fi
  while /bin/true; do
    read -rp "${PROMPT}"  RESP
    if [[ -z "${RESP}" ]]; then
      RESP="${DEFAULT}"
    fi
    case "${RESP}" in
      ["${OPTIONS}"]*) return 0;
    esac
    echo "Invalid response: ${RESP} must be one of ${OPTIONS}"
  done
}

def _confirm_default_yes():
  # Ask the user for confirmation, defaulting to "yes."
  # Returns true if the user confirms.
  local _PROMPT RESP
  _PROMPT="$*"
  if [[ -z "${_PROMPT}" ]]; then
    _PROMPT="Confirm? (y/N)  "
  fi
  if (( YESYESYES )); then echo "${_PROMPT}: yesyesyes"; return 0; fi
  read -rp "${_PROMPT}" RESP
  case "${RESP}" in
    [Nn]*) return 1 ;;
    *)     return 0 ;;
  esac
}

def _confirm_default_no():
  # Ask the user for confirmation, defaulting to "no."
  # Returns true if the user confirms.
  local _PROMPT RESP
  _PROMPT="$*"
  if [[ -z "${_PROMPT}" ]]; then
    _PROMPT="Confirm? (y/N)  "
  fi
  if (( YESYESYES )); then echo "${_PROMPT}: yesyesyes"; return 0; fi
  read -rp "${_PROMPT}" RESP
  case "${RESP}" in
    [Yy]*) return 0 ;;
    *)     return 1 ;;
  esac
}

def _confirm_or_exit():
  # Ask the user for confirmation, defaulting to "no."
  # Terminates gee if the user does not confirm.
  if ! _confirm_default_no "$@"; then
    _fatal "Exiting."
  fi
}

# "The developer wants to read this."
def _debug():
  if (( "${DEBUG:-0}" > 0 )); then
    printf >&2 "${_COLOR_DBG}DBG: %s${_COLOR_RST}\n" "$@"
  fi
}

# "The user wants to read this."
def _info():
  printf >&2 "${_COLOR_INFO}%s${_COLOR_RST}\n" "$@"
}

# "The user needs this to visually separate information."
def _banner():
  local COLS LEN BAR BLANK
  COLS="$(safe_tput cols)"
  COLS="$(( COLS - 1 ))"
  LEN="$( printf "%s\n" "$@" | wc -L )"
  if (( LEN > (COLS-4) )); then
    LEN="$(( COLS - 4 ))"
  fi
  BAR="$(head -c "$(( LEN + 4 ))" /dev/zero | tr '\0' '#')"
  BLANK="$(head -c "$(( COLS - LEN - 4 ))" /dev/zero | tr '\0' ' ')"
  printf >&2 "\n"
  printf >&2 "%s%s%s\n" "${_COLOR_BANNER}" "${BAR}" "${BLANK}"
  printf >&2 "# %-${LEN}.${LEN}s #${BLANK}\n" "$@"
  printf >&2 "%s%s%s\n" "${BAR}" "${BLANK}" "${_COLOR_RST}"
}


# Warn the user.  "The user should know this."
def _warn():
  printf >&2 "${_COLOR_WARN}WARNING: %s${_COLOR_RST}\n" "$@"
}

# Use _fatal for user errors that don't require a stack dump.
# "The user is going to be sad when they see this."
def _fatal():
  printf >&2 "${_COLOR_DIE}FATAL: %s${_COLOR_RST}\n" "$@"
  ABNORMAL=0  # we died intentionally.
  exit 1
}

# Use _die to report internal errors that require a stack dump.
# "The user is going to be mad when they see this."
def _die():
  printf >&2 "${_COLOR_DIE}FATAL: %s${_COLOR_RST}\n" "$@"
  if [[ -z "${NOSTACK}" ]]; then
    echo >&2 "Stack trace:"
    local i
    i=0
    while caller "${i}"; do
      (( i++ ))
    done
  fi
  printf >&2 "Please notify: jonathan@enfabrica.net\n"
  exit 1
}

def _cmd():
  # Run a command and generate associated log messages.
  local COLS ESCAPED_CMD
  COLS="$(safe_tput cols)"
  ESCAPED_CMD="$( printf " %q" "$@")"
  if [[ $DRYRUN -eq 0 ]]; then
    if [[ $VERBOSE -gt 0 ]]; then
      local C
      C=$(( COLS - 4 ))
      printf >&2 "${_COLOR_CMD}CMD:%-${C}s${_COLOR_RST}\n" "${ESCAPED_CMD}"
    fi
    set +e
    "$@"
    RC=$?
    if [[ "${RC}" -ne 0 ]]; then
      _warn "Command failed with exit code ${RC}"
      if [[ -n "${NOFAIL}" ]]; then
        RC=0
      fi
    fi
    set -e
    return "${RC}"
  else
    local C
    C=$(( COLS - 7 ))
    printf >&2 "${_COLOR_CMD}DRYRUN:%-${C}s${_COLOR_RST}\n" "${ESCAPED_CMD}"
  fi
}

def _read_cmd():
  # Run a command, and save each line of output to an array variable.
  local VAR="$1"; shift

  local COLS ESCAPED_CMD
  COLS="$(safe_tput cols)"
  ESCAPED_CMD="$( printf " %q" "$@")"
  if [[ $DRYRUN -eq 0 ]]; then
    if [[ $VERBOSE -gt 0 ]]; then
      local C
      C=$(( COLS - 4 ))
      printf >&2 "${_COLOR_CMD}CMD:%-${C}s${_COLOR_RST}\n" "${ESCAPED_CMD}"
    fi
    set +e
    mapfile -t "${VAR}" < <("$@"; printf "%d\n" "$?")
    local TMP
    TMP="${VAR}[-1]"
    RC="${!TMP}"  # indirect
    unset "${VAR}[-1]"
    if [[ "${RC}" -ne 0 ]]; then
      _warn "Command returned non-zero exit code: ${RC}"
      if [[ -n "${NOFAIL}" ]]; then
        RC=0
      fi
    fi
    set -e
    return "${RC}"
  else
    local C
    C=$(( COLS - 7 ))
    printf >&2 "${_COLOR_CMD}DRYRUN:%-${C}s${_COLOR_RST}\n" "${ESCAPED_CMD}"
  fi
}

def _gh():
  # Invoke gh
  _cmd "${GH}" "$@"
}

def _git():
  # Invoke git
  _cmd "${GIT}" "$@"
}

def _silent_cmd():
  # Run a command and don't write anything to stdout.
  local ESCAPED_CMD
  ESCAPED_CMD="$( printf " %q" "$@")"
  if [[ $DRYRUN -eq 0 ]]; then
    set +e
    "$@" > /dev/null
    RC=$?
    set -e
    if [[ "${RC}" -ne 0 ]]; then
      _info "CMD: ${ESCAPED_CMD}"
      if [[ -z "${NOFAIL}" ]]; then
        _fatal "Command failed with exit code ${RC}"
      else
        _warn "Command failed with exit code ${RC}"
      fi
    fi
    return "${RC}"
  fi
}

def _git_can_fail():
  # Invoke git, but don't exit if git returns a non-zero exit code.
  NOFAIL=1 _git "$@"
}

def _install_tools():
  # Checks for and installs any missing tools.

  # If your dev image is missing the github-cli tool, this should install it.
  if [ ! -x "${GH}" ]; then
    _info "Installing missing tool: gh"
    local KRFILE; KRFILE="/usr/share/keyrings/githubcli-archive-keyring.gpg"
    /usr/bin/curl -fsSL \
      https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | sudo /usr/bin/apt-key --keyring "${KRFILE}" add -
    echo "deb [arch=amd64 signed-by=${KRFILE}] https://cli.github.com/packages stable main" \
      | sudo /usr/bin/tee /etc/apt/sources.list.d/githubcli-archive.list
    sudo /usr/bin/apt-get update || /bin/true  # ignore errors associated with stale repos.
    sudo /usr/bin/apt-get install gh
    if [ ! -x ${GH} ]; then
      _fatal "Could not install gh."
    fi
  fi

  if [ ! -x "${JQ}" ]; then
    _info "Installing missing tool: jq"
    sudo /usr/bin/apt-get update || /bin/true  # ignore errors associated with stale repos.
    sudo /usr/bin/apt-get install jq
    if [ ! -x "${JQ}" ]; then
      _fatal "Could not install jq."
    fi
  fi
}

def _count_diffs():
  # Could the number of files that are different between two branches.
  local BRANCH_A BRANCH_B DIFFS
  BRANCH_A="$1"
  BRANCH_B="$2"
  "${GIT}" diff --name-only "${BRANCH_A}..${BRANCH_B}" | wc -l
}

def _branch_has_unstaged_changes():
  if "${GIT}" diff --quiet; then
    # RC=0 means no differences, so return false
    return 1
  else
    # RC=1 means differences were found, so return true
    return 0
  fi
}

def _branch_ahead_behind():
  # Report how many commits this branch is ahead/behind another branch.
  local branch; branch="$1"
  local obr="$2";
  _read_parents_file
  if [[ -z "${obr}" ]]; then
    obr="$(_get_parent_branch "${branch}")"
  fi
  local -a counts
  read -r -a counts < <("${GIT}" rev-list --left-right --count "${branch}...${obr}") || /bin/true
  if (( "${counts[0]}" == 0 )) && (( "${counts[1]}" == 0 )); then
    printf "%-20s: same as %s" "${branch}" "${obr}"
  else
    printf "%-20s: %s ahead, %s behind %s" "${branch}" "${counts[0]}" "${counts[1]}" "${obr}"
  fi
  if _remote_branch_exists origin "${branch}"; then
    counts=()
    read -r -a counts < <("${GIT}" rev-list --left-right --count "${branch}...origin/${branch}") || /bin/true
    if (( counts[1] > 0 )); then
      printf ", %d behind %s" "${counts[1]}" "origin/${branch}"
    fi
  fi
  printf "\n"
}

def _remote_branch_exists():
  # Returns true if a remote branch exists (on "origin").
  local REPO="$1"; shift  # origin?
  local BRANCH="$1"; shift
  if [[ -z "${BRANCH}" ]]; then
    _die "insufficient args"
  fi
  local OUTPUT
  OUTPUT="$("${GIT}" ls-remote "${REPO}" "${BRANCH}")"
  if [[ -z "${OUTPUT}" ]]; then
    return "${FALSE}"
  else
    return "${TRUE}"
  fi
}

def _local_branch_exists():
  # Returns true if a local branch exists (even if it's worktree was deleted).
  # Fixes up the worktree if the branch is missing from the worktree.
  local BRANCH="$1"; shift
  _set_main
  if ! "${GIT}" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    return 1  # false
  fi

  # branch exists, but let's double check that worktree is set up correctly:
  local BRDIR
  BRDIR="${REPO_DIR}/${BRANCH}"
  if ! [[ -d "${BRDIR}" ]]; then
    # try to fix worktree.
    _git worktree add "${REPO_DIR}/${BRANCH}"
    _info "Created ${BRDIR}"
  fi
  _update_branch_to_worktree
  BRDIR="${BRANCH_TO_WORKTREE["${BRANCH}"]}"
  if [[ -n "${BRDIR}" ]]; then
    return 0
  else
    _fatal "Branch ${BRANCH} exists but could not create worktree."
    return 1
  fi
}

def _open_rebase_shell():
    # Opens an interactive subshell for resolving conflicts during a rebase.
    # This is the "old" flow, and it's a bit messy.
    # TODO(jonathan): make the prompts less noisy.
    local BOLD RST
    BOLD="$(safe_tput bold)"
    RST="$(safe_tput sgr0)"
    export GEE_HELP=""
    GEE_HELP+="You are interactively rebasing a branch with conflicts.${NEWLINE}"
    GEE_HELP+="  To see where conflicts are: ${BOLD}git status${RST}${NEWLINE}"
    GEE_HELP+="  To run a 3-way merge tool: ${BOLD}git mergetool${RST}${NEWLINE}"
    GEE_HELP+="  To mark a file as fixed: ${BOLD}git add <file>${RST}${NEWLINE}"
    GEE_HELP+="  To continue rebase after fixing: ${BOLD}git rebase --continue${RST}${NEWLINE}"
    GEE_HELP+="  To give up and go back to original state: ${BOLD}git rebase --abort${RST}${NEWLINE}"
    GEE_HELP+="  To return to gee: ${BOLD}exit${RST}${NEWLINE}"
    _git status
    _banner "Entering subshell: resolve conflicts or abort and then exit."
    echo "${GEE_HELP}"
    set +e
    bash --noprofile --norc
    _banner "Subshell terminated with exit code $?."
    set -e
}

def _interactive_conflict_resolution():
  # The function walks the user through a rebase operation, one conflict at a
  # time, and gives the user the option to use their file, the upstream file,
  # skip the commit, run a mergetool, or abort.
  local PARENT CHILD
  PARENT="$1"
  CHILD="$2"
  ONTO="$3"
  if [[ -z "${CHILD}" ]]; then
    _die "Must specify branch name."
  fi
  local CHILD_ROOT
  CHILD_ROOT="${BRANCH_TO_WORKTREE["${CHILD}"]}"
  cd "${CHILD_ROOT}"
  local ABORT=0
  while _is_rebase_in_progress; do
    local -a STATUS=()
    mapfile -t STATUS < <( "${GIT}" status --porcelain )
    # We're merging onto this commit:
    local ONTO_COMMIT ONTO_DESC
    ONTO_COMMIT="$("${GIT}" rev-parse HEAD)"
    ONTO_DESC="$("${GIT}" show --oneline -s "${ONTO_COMMIT}")"
    # This is the commit we're trying to apply:
    local FROM_COMMIT FROM_DESC
    FROM_COMMIT="$("${GIT}" rev-parse REBASE_HEAD)"
    FROM_DESC="$("${GIT}" show --oneline -s "${FROM_COMMIT}")"

    _banner "Attempting to apply: ${FROM_DESC}" \
            "               onto: ${ONTO_DESC}"
    local STATUS_LINE DONE SKIP RESTART
    SKIP=0
    RESTART=0
    if [[ "${#STATUS[@]}" -eq 0 ]]; then
      _warn "Empty commit, skipping automatically."
      SKIP=1
    fi
    for STATUS_LINE in "${STATUS[@]}"; do
      local DECODED_ST ST FILE
      read -r ST FILE <<< "${STATUS_LINE}"
      case "${ST}" in
        # TODO(jonathan): do I have "us" and "them" backwards here?
        ?) DECODED_ST="" ;;  # The no-conflict case
        DD) DECODED_ST="Both deleted" ;;
        AU) DECODED_ST="Added by us" ;;
        UD) DECODED_ST="Deleted by them" ;;
        UA) DECODED_ST="Added by them" ;;
        DU) DECODED_ST="Deleted by us" ;;
        AA) DECODED_ST="Both added" ;;
        UU) DECODED_ST="Both modified" ;;
        *)  DECODED_ST="Bizarre!" ;;
      esac
      if [[ -z "${DECODED_ST}" ]]; then
        _info "${FILE}: no conflicts."
        continue
      fi
      _info "${FILE}: ${ST}=${DECODED_ST}"

      DONE=0
      while (( DONE == 0 )); do
        local M RESP
        if (( YESYESYES )); then
          _warn "YESYESYES is enabled: taking newer version automatically."
          RESP="N";
        else
          read -r -n1 -p \
            "Keep (O)ld, (N)ew, (M)erge, (G)ui, (P)ick, (V)iew, s(K)ip, or (A)bort? " \
            RESP
          printf "\n"
        fi
        # https://stackoverflow.com/questions/25576415/what-is-the-precise-meaning-of-ours-and-theirs-in-git
        # During a rebase operation:
        # "Ours" = branch being merged into = older version of file.
        # "Theirs" = branch being merged from = newer version of file.
        case "${RESP}" in
          [Nn])
            _info "Keeping newer ${FILE} from ${FROM_DESC}"
            "${GIT}" checkout --theirs "${FILE}"
            DONE=1
            ;;
          [Oo])
            _info "Keeping older ${FILE} from ${ONTO_DESC}"
            "${GIT}" checkout --ours "${FILE}"
            DONE=1
            ;;
          [Mm])
            _git mergetool "${FILE}"
            M="$("${GIT}" diff --check "${FILE}" | grep -c "conflict marker" | cat)"
            if (( M == 0 )); then
              DONE=1
            else
              _warn "${FILE} still contains conflict markers."
            fi
            ;;
          [Gg])
            local GUI_MERGETOOL
            GUI_MERGETOOL="$(git config --get merge.guitool)"
            if [[ -z "${GUI_MERGETOOL}" ]]; then
              GUI_MERGETOOL=meld  # default
            fi
            _git mergetool --tool="${GUI_MERGETOOL}" --no-prompt "${FILE}"
            M="$("${GIT}" diff --check "${FILE}" | grep -c "conflict marker" | cat)"
            if (( M == 0 )); then
              DONE=1
            else
              _warn "${FILE} still contains conflict markers."
            fi
            ;;
          [Pp])
            _warn "\"Pick\" will abort and restart your rebase merge from the beginning."
            if _confirm_default_no "Are you sure you want to restart? (y/N)  "; then
              _git rebase --abort
              local -a GITCMD=( rebase --no-autostash -i )
              if [[ -n "${ONTO}" ]]; then
                GITCMD+=( --onto "${ONTO}" )
              fi
              GITCMD+=( "${PARENT}" "${CHILD}" )
              if ! _git "${GITCMD[@]}"; then
                if ! _is_rebase_in_progress; then
                  # This should never happen.
                  _die "Rebase command failed, but rebase is not in progress.  Bug!"
                fi
                _warn "Rebase operation had merge conflicts."
              else
                _info "Rebase succeeded."
              fi
              RESTART=1
              DONE=1
            fi
            ;;
          [Ss])
            _open_rebase_shell
            RESTART=1  # go back to beginning, maybe we're done?
            DONE=1
            ;;
          [Vv])
            _git show --color --pretty=format:%b "${FROM_COMMIT}" | less -R
            ;;
          [Kk])
            _info "Skipping commit: ${FROM_DESC}"
            DONE=1
            SKIP=1
            ;;
          [Aa])
            _info "Aborting rebase, and resetting."
            DONE=1
            ABORT=1
            ;;
          *)
            _warn "Invalid choice: ${RESP}"
            _info \
              "Options are:" \
              "  n: Keep (N)ewer file (from the patch being applied)." \
              "  o: Keep (O)lder file (from the version being patched)." \
              "  m: (M)erge: Runs \"git mergetool\" on this file." \
              "  g: (G)UI Merge: Runs \"git mergetool --gui\" on this file." \
              "  p: (P)ick: Aborts rebase and re-runs \"git rebase -i\"." \
              "  s: (S)hell: Opens a bash sub-shell for expert use." \
              "  v: (V)iew: Show the patch being applied." \
              "  k: s(K)ip: Skips a whole commit (all files)." \
              "  a: (A)bort: Aborts and returns branch to original state."
            ;;
        esac
      done  # DONE

      if (( SKIP == 1 )); then break; fi
      if (( ABORT == 1 )); then break; fi
      if (( RESTART == 1 )); then break; fi
    done  # STATUS_LINE

    if (( SKIP == 1 )); then
      _git_can_fail rebase --skip | cat
    fi
    if (( ABORT == 1 )); then break; fi
    if (( RESTART == 1 )); then continue; fi

    local -a MARKERS
    mapfile -t MARKERS < <( "${GIT}" diff --check | grep "conflict marker" | cat )

    if (( "${#MARKERS[@]}" == 0 )); then
      _git add .
      _git_can_fail rebase --continue
    else
      _warn "Cannot proceed with rebase: conflict markers still present."
      _info "${MARKERS[@]}"
      _info "Try again..."
    fi
  done  # while rebase is in progress

  if (( ABORT == 1 )); then
    _git rebase --abort
    return
  fi
}

def _safer_rebase():
  # Performs a safe version of "git rebase $PARENT $CHILD".  If a merge
  # conflict occurs, invokes gee's conflict resolution flow.
  #
  # If ONTO is specified, performs "git rebase --onto $ONTO $PARENT $CHILD"
  # This last form resets $CHILD to the head of $ONTO, and then replays the
  # sequence of commits in $CHILD that are not in $PARENT.
  local CHILD="$1"
  local PARENT="$2"
  local ONTO="$3"  # optional
  local MB=""

  # check if child branch has an outstanding PR:
  local -a OPEN_PRS
  mapfile -t OPEN_PRS < <( _list_open_pr_numbers )
  if (( "${#OPEN_PRS[@]}" )); then
    _warn "Open PR exists for branch ${CHILD}: ${OPEN_PRS[*]}"
    _info "If a reviewer is already looking at your PR, rebasing this branch" \
          "will break the reviewer's ability to see what has changed when" \
          "you commit new changes.  Are you sure you want to do this?"
    if ! _confirm_default_no ; then
      _warn "Skipped update of branch ${CHILD}."
      return 0
    fi
  fi

  # update BRANCH_TO_WORKTREE before rebasing, as this information
  # becomes unavailable when in detached head mode:
  _update_branch_to_worktree

  # TODO(jonathan): check if origin is ahead of local, and if so, do a git pull --rebase
  # operation.  This will allow multi-homed gee to work better.

  _checkout_or_die "${CHILD}"

  # Check if we're rebasing onto a branch with uncommitted changes:
  local -a UNCOMMITTED
  mapfile -t UNCOMMITTED < <( "${GIT}" status --short -uall )
  if (( "${#UNCOMMITTED[@]}" )); then
    _warn "Branch ${CHILD} contains ${#UNCOMMITTED[@]} uncommitted changes:" \
      "${UNCOMMITTED[@]}"
    _fatal "Commit all changes and try again."
  fi

  _silent_cmd "${GIT}" tag -f "${CHILD}.REBASE_BACKUP"
  local CHILD_DIR
  CHILD_DIR="$(_get_branch_rootdir "${CHILD}")"
  cd "${CHILD_DIR}"
  local PARENT_HEAD
  if [[ "${PARENT}" == "upstream/"* ]]; then
    PARENT_HEAD="$(git ls-remote upstream "${PARENT#upstream/}" | awk '{print $1}')"
  else
    PARENT_HEAD="$(git show-ref "refs/heads/${PARENT}" | awk '{print $1}')"
  fi

  # Use rebase for local parent branches, or pull for remote parent branches.
  local -a GITCMD=()
  if [[ "${PARENT}" == "upstream/"* ]]; then
    if [[ -n "${ONTO}" ]]; then
      # TODO(jonathan): figure out the right way to split this into git
      # fetch/rebase steps, so --onto can work -- though we are unlikely
      # to ever need this (squash-merge from a fork of another's PR?).
      #
      # I wonder what I'm doing wrong?  Naively:
      # $ git fetch upstream
      # $ git rebase upstream/pull/1234/head
      # fatal: invalid upstream 'upstream/pull/1234/head'
      _die "git pull --rebase does not support --onto"
    fi
    GITCMD+=( pull --rebase --no-autostash upstream "${PARENT#upstream/}" )
  else
    GITCMD+=( rebase --no-autostash )
    if [[ -n "${ONTO}" ]]; then
      GITCMD+=(--onto "${ONTO}")
    fi
    GITCMD+=( "${PARENT}" "${CHILD}" )
  fi

  _checkout_or_die "${CHILD}"
  if ! _git "${GITCMD[@]}"; then
    if ! _is_rebase_in_progress; then
      # This should never happen.
      _die "Rebase command failed, but rebase is not in progress.  Bug!"
    fi
    _warn "Rebase operation had merge conflicts."
    _interactive_conflict_resolution "${PARENT}" "${CHILD}" "${ONTO}"

    if _is_rebase_in_progress; then
      local STATUS
      mapfile -t STATUS < <( "${GIT}" status );
      _warn "${STATUS[@]}"
      _warn "Merge conflict in branch ${CHILD}, must be manually resolved."
      _fatal "Exited without resolving rebase conflict."
    fi

    if "${GIT}" merge-base --is-ancestor "${PARENT_HEAD}" HEAD; then
      _info "Rebase merge confirmed."
    else
      _warn "Rebase did not succeed, aborting."
      return 1
    fi
  fi

  _info "To undo: git checkout ${CHILD}; git reset --hard ${CHILD}.REBASE_BACKUP"
  _push_to_origin "+${CHILD}"
}

def _is_rebase_in_progress():
  # Is this branch currently in the middle of a rebase operation?
  local G
  G="$(git rev-parse --git-dir)"
  if compgen -G "${G}/rebase*" > /dev/null; then
    return 0  # 0=true
  else
    return 1  # 1=false
  fi
}

def _push_to_origin():
  local B="$1"
  if [[ -z "${B}" ]]; then
    B="$(_get_current_branch)"
  fi
  _git push --quiet -u origin "${B}"
}


def _checkout_or_die():
  # we want to check out a branch.  Maybe there is already a
  # worktree.  Maybe we need to create a worktree.  Let's do
  # whatever is necessary.
  local BRANCH BRDIR
  BRANCH="$1"; shift
  # Do we already have a worktree?
  BRDIR="${REPO_DIR}/${BRANCH}"
  if ! [[ -d "${BRDIR}" ]]; then
    _git worktree add "${REPO_DIR}/${BRANCH}"
    BRDIR="$(_get_branch_rootdir "${BRANCH}")"
    _info "Created ${BRDIR}"
  fi
  cd "${BRDIR}"
  local CUR_BRANCH
  CUR_BRANCH="$(_get_current_branch)"
  if [[ "${CUR_BRANCH}" != "${BRANCH}" ]]; then
    _warn "${BRDIR} pointed to branch ${CUR_BRANCH} instead of ${BRANCH}."
    _git checkout "${BRANCH}"
    _info "... Fixed."
  fi
}

def _in_gee_repo():
  # Return "main" if we are outside of a gee repo:
  if [[ "${PWD}" =~ ^"${HOME}"/gee/[a-zA-Z0-9_-]+ ]]; then
    return "${TRUE}"
  else
    return "${FALSE}"
  fi
}

def _in_gee_branch():
  # Return "main" if we are outside of a gee repo:
  if [[ "${PWD}" =~ ^"${HOME}"/gee/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+ ]]; then
    return "${TRUE}"
  else
    return "${FALSE}"
  fi
}

def _get_current_branch():
  # Outside of a gee repo, current branch is always "main":
  _set_main
  if ! _in_gee_repo; then
    echo "${MAIN}"
    return
  fi
  # Inside of a gee repo, use git rev-parse:
  local CB
  CB="$("${GIT}" rev-parse --abbrev-ref HEAD)"
  if [[ -z "${CB}" ]]; then
    _die "Could not get current branch in directory $("${PWD}")"
  fi
  echo "${CB}"
}

def _update_from_upstream():
  local LOCAL_BRANCH="$1"
  local UPSTREAM_BRANCH="$2"
  _checkout_or_die "${LOCAL_BRANCH}"
  if ! _git pull --rebase --no-autostash upstream "${UPSTREAM_BRANCH}"; then
    _warn "Git pull operation failed."
    if ! _is_rebase_in_progress; then
      _fatal "Rebase not in progress, no idea what went wrong."
    fi
    _interactive_conflict_resolution "upstream/${UPSTREAM_BRANCH}" "${LOCAL_BRANCH}"
    if _is_rebase_in_progress; then
      local STATUS
      mapfile -t STATUS < <( "${GIT}" status );
      _warn "${STATUS[@]}"
      _warn "Merge conflict in branch ${LOCAL_BRANCH}, must be manually resolved."
      _fatal "Exited without resolving rebase conflict."
    fi
  fi

  local -a COUNTS=()
  read -r -a COUNTS < \
    <( "${GIT}" rev-list --left-right --count "upstream/${UPSTREAM_BRANCH}...${LOCAL_BRANCH}" | cat) \
    || /bin/true
  if (( COUNTS[0] != 0 )); then
    _fatal "pull failed: upstream/${UPSTREAM_BRANCH} is ${COUNTS[1]} commits ahead of ${LOCAL_BRANCH}"
  fi
  _push_to_origin "+${MAIN}"
}

def _update_main():
  # Merge from upstream/main into main:
  local BRANCH
  BRANCH="$(_get_current_branch)"
  _set_main
  _checkout_or_die "${MAIN}"
  # check for local changes
  local -a CHANGES
  read -r -a CHANGES < \
    <( "${GIT}" diff --name-only | cat) \
    || /bin/true
  if [[ "${#CHANGES[@]}" != 0 ]]; then
    _warn "${MAIN} branch contains uncommitted changes."
  fi
  # TODO(jonathan): maybe just use _safer_rebase here instead.
  _update_from_upstream "${MAIN}" "${MAIN}"
  _checkout_or_die "${BRANCH}"
}

# The parents file keeps track of two things:
#   - parent: the branch that spawned the current branch
#   - mb: the commit id of the mergebase with the parent branch
#
# mb is updated every time a branch is rebased, and is used to keep track of
# the last mergebase of a child branch, even if the parent branch gets rebased.
# This allows us to defer rebase operations of children, even after parents get
# rebased.
#
# TODO(jonathan): is there a better way to track this information?
# TODO(jonathan): maybe we don't need to track MB after all?

# Lazy-load parents metadata:
def _read_parents_file():
  # Update the global PARENTS associative array from a file.
  if (( PARENTS_FILE_IS_LOADED )); then
    return
  fi
  local PATH="${REPO_DIR}/.gee/parents"
  local KEY VALUE
  if [[ ! -d "${REPO_DIR}/.gee" ]]; then
    /usr/bin/mkdir "${REPO_DIR}/.gee"
  fi
  if [[ ! -f "${PATH}" ]]; then
    /usr/bin/touch "${PATH}"
  fi
  local BRANCH PARENT MB
  while read -r BRANCH PARENT MB; do
    PARENTS["${BRANCH}"]="${PARENT}"
    MERGEBASES["${BRANCH}"]="${MERGEBASE}"
  done < "${PATH}" \
    || /bin/true
  PARENTS_FILE_IS_LOADED=1
}

def _write_parents_file():
  # Write back the global PARENTS associative array to a file.
  if ! (( PARENTS_FILE_IS_LOADED )); then
    return
  fi
  if [[ "${#PARENTS[@]}" -eq 0 ]]; then
    _warn "Almost wrote empty parents file!"
    return
  fi
  local PATH="${REPO_DIR}/.gee/parents"
  if [[ ! -d "${REPO_DIR}/.gee" ]]; then
    /usr/bin/mkdir "${REPO_DIR}/.gee"
  fi
  local KEY VALUE MB
  for KEY in "${!PARENTS[@]}"; do
    VALUE="${PARENTS["$KEY"]}"
    MB="${MERGEBASES["$KEY"]}"
    printf "%q %q %q\n" "${KEY}" "${VALUE}" "${MB}"
  done > "${PATH}"
}

ABNORMAL=1
def _cleanup():
  local lineno="$1"
  # Ensure we always save the PARENTS file, even if we die:
  _write_parents_file
  # Warn the user if we terminated abnormally (for example, if set -e fired).
  if (( ABNORMAL == 1 )); then
    _warn "Abnormal termination at line ${lineno}"
  fi
}
trap '_cleanup "${LINENO}"' EXIT
# This doesn't quite work right, which is why gee needs to be rewritten
# in golang:
# def _error_report():
#   local lineno="$1"
#   _warn "ERR signal raised on line ${lineno}"
# }
# trap '_error_report "${LINENO}"' ERR

def _join():
  # Usage:  _join <delimiter> <elements...>
  # Example: _join "," "${ARRAY[@]}"
  local DELIM="$1"; shift
  local TEXT="$1"; shift
  TEXT+="$(printf "${DELIM}%s" "$@")"
  echo "${TEXT}"
}

def _get_pull_requests():
  # Prints list of PRs associated with this branch.
  local BRANCH="$1"
  if [[ -z "${BRANCH}" ]]; then
    BRANCH="$(_get_current_branch)"
  fi
  # "gh pr view" arguments are similar to "gh pr create" but not identical.
  local FROM_BRANCH="${GHUSER}:${BRANCH}"
  if [[ "${UPSTREAM}" == "${GHUSER}" ]]; then
    # gh-cli treats this as a special case for some reason.
    FROM_BRANCH="${BRANCH}"
  fi
  local Q
  Q='if .isDraft then ["DRAFT", .number, .title] '
  Q+='else [.state, .number, .title] '
  Q+='end | join(" ")'
  "${GH}" pr view \
    --repo="${UPSTREAM}/${REPO}" "${FROM_BRANCH}" \
    --json "state,number,title,isDraft" \
    --jq "${Q}" \
    | cat
}

def _list_open_pr_numbers():
  local BRANCH="$1"
  if [[ -z "${BRANCH}" ]]; then
    BRANCH="$(_get_current_branch)"
  fi
  _get_pull_requests "${BRANCH}" | grep ^OPEN | awk '{print $2}'
}

def _list_merged_pr_numbers():
  local BRANCH="$1"
  if [[ -z "${BRANCH}" ]]; then
    BRANCH="$(_get_current_branch)"
  fi
  _get_pull_requests "${BRANCH}" | grep ^MERGED | awk '{print $2}'
}

def _gh_pr_view():
  # Normalize the interface to calling "gh pr view"
  local CURRENT_BRANCH;
  CURRENT_BRANCH="$(_get_current_branch)"
  local FROM_BRANCH="${GHUSER}:${CURRENT_BRANCH}"
  if [[ "${UPSTREAM}" == "${GHUSER}" ]]; then
    # gh-cli treats this as a special case for some reason.
    FROM_BRANCH="${CURRENT_BRANCH}"
  fi
  _gh pr view --repo "${UPSTREAM}/${REPO}" "${FROM_BRANCH}"
}

def _check_pr_description():
  local PATH="$1"
  local -a LINES
  mapfile -t LINES <"${PATH}"
  if [[ "${#LINES[@]}" -lt 3 ]]; then
    _warn "The description must contain at least a title, a blank line, and a body."
    return 1
  fi
  if [[ -z "${LINES[0]}" ]]; then
    _warn "The first line of the description must be the title."
    return 1
  fi
  if [[ -n "${LINES[1]}" ]]; then
    _warn "The second line of the description must be blank."
    return 1
  fi
  if [[ -z "${LINES[2]}" ]]; then
    _warn "The third line must be the beginning of the longer description."
    return 1
  fi
  return 0
}

# _print_codeowners prints a codeowners report for the opened files
# in this branch.  It also annotates each user with their associated
# review state (ie, COMMENTED, APPROVED) if available.  Note that
# for this feature to work, CODEOWNERS must be expressed as a list
# of github usernames, not email addresses.
def _print_codeowners():
  _check_cwd
  _set_main

  local -A RMAP=()
  local REVIEWER STATE
  while read -r REVIEWER STATE; do
    RMAP["${REVIEWER}"]="${STATE}"
  done < <(
    ${GH} pr view --json reviews \
      --jq ".reviews[] | [.author.login, .state] | @tsv" \
      2>/dev/null \
      | cat
  )

  local FILES=()
  mapfile -t FILES < <(
    (
      "${GIT}" diff --name-only "${MAIN}...HEAD";
      "${GIT}" diff --name-only;
    ) | sort -u; /bin/true )
  if [[ "${#FILES[@]}" -eq 0 ]]; then
    _warn "There are no tracked, modified files in this client."
    return 0
  fi

  local ROOT
  ROOT="$(_get_branch_rootdir)"
  local RULES=()
  if ! [[ -f "${ROOT}/CODEOWNERS" ]]; then
    _warn "No CODEOWNERS file was found in ${ROOT}."
    return 0
  fi
  mapfile -t RULES < <( \
    sed -e 's/[[:space:]]*#.*//' < "${ROOT}/CODEOWNERS" \
    | grep -v -e '^[[:space:]]*$' )

  # Check each file against CODEOWNERS rules, in order.  The last matching
  # rule determines the owners associated with that file.
  local F O R
  for F in "${FILES[@]}"; do
    O=""
    for R in "${RULES[@]}"; do
      local EXP VAL
      read -r EXP VAL <<<"${R}"
      if [[ "$EXP" == "/"* ]]; then
        if [[ "/$F" == "${EXP}"* ]]; then
          O="${VAL}"
        fi
      elif [[ "$EXP" == *"/" ]]; then
        if [[ "/$F" == */"${EXP}"* ]]; then
          O="${VAL}"
        fi
      elif [[ "$EXP" == "*"* ]]; then
        if [[ "/$F" == *"${EXP:1}" ]]; then
          O="${VAL}"
        fi
      else
        if [[ "/$F" == *"/${EXP}" ]]; then
          O="${VAL}"
        fi
      fi
    done
    if [[ -n "${O}" ]]; then
      local -a owners=()
      mapfile -t owners < <( echo "${O}" | xargs -n1 | sort -u )
      local owner
      for owner in "${owners[@]}"; do
        local oname="${owner}"
        if [[ "${oname}" == @* ]]; then
          oname="${oname:1}"
        fi
        if [[ "${RMAP["${oname}"]+found}" ]]; then
          echo "${owner}(${RMAP["${oname}"]})"
        else
          echo "${owner}"
        fi
      done | xargs
    fi
  done | sort -u
}

# Returns true iff this is the latest available version of gee.
def _check_gee_version_is_current():
  local GEE_PATH CUR_MD5 NEW_MD5
  GEE_PATH="$(readlink -f "$0")"
  CUR_MD5="$(md5sum "${GEE_PATH}" | awk '{print $1}')"
  NEW_MD5=""
  local -a ASTORE_OUTPUT=()
  if _read_cmd ASTORE_OUTPUT "${ENKIT}" astore list "${GEE_ON_ASTORE}"; then
    local -a FIELDS
    read -r -a FIELDS <<< "${ASTORE_OUTPUT[2]}"
    if [[ "${#FIELDS[@]}" -eq 10 ]]; then
      NEW_MD5="${FIELDS[5]}"
    else
      _warn "Unexpected output from astore:" "${ASTORE_OUTPUT[2]}"
    fi
  else
    _warn "Could not access astore, perhaps you need to run \"enkit auth\"?"
  fi

  if [[ -z "${NEW_MD5}" ]]; then
    _warn "Could not check for a newer version of gee."
    return
  fi

  if [[ "${CUR_MD5}" == "${NEW_MD5}" ]]; then
    return 0
  else
    return 1
  fi
}


##########################################################################
# init command
##########################################################################

_register_help "init" "initialize a new git workspace" <<'EOT'
Usage: gee init [<repo>]

Arguments:

   repo: Specifies which enfabrica repository to check out.
         If repo is not specified, `internal` is used by default.

`gee init` creates a new gee-controlled workspace in the user's home directory.
The directory `~/gee/<repo>/main` will be created and populated, and all
other branches will be checked out into `~/gee/<repo>/<branch>`.
EOT

def gee__init():
  local R
  R="${1:-"${REPO}"}"
  REPO="${R}"
  REPO_DIR="${GEE_DIR}/${REPO}/"
  if (( "${TESTMODE}" )); then
    REPO_DIR="${HOME}/testgee/${REPO}/"
  fi

  # ensure all tools are installed.
  _install_tools

  if ! _check_ssh; then
    _warn "Cannot connect to github over ssh."
    _confirm_or_exit "Would you like to set up ssh access now? (y/N)  "
    _ssh_enroll
  fi

  gee__hello

  # _set_main won't work without a cloned repo, so assume MAIN for now
  # and fix the branch name later.
  MAIN=main
  _gh config set git_protocol ssh
  _check_gh_auth

  _info "Initializing ${REPO_DIR} for ${REPO}/${MAIN}"

  if [[ -d "${REPO_DIR}/${MAIN}" ]]; then
    _fatal \
      "Initialized workspace already exists in ${REPO_DIR}"
  fi
  _cmd mkdir -p "${REPO_DIR}/.gee"
  local URL UPSTREAM_URL
  URL="${GIT_AT_GITHUB}:${GHUSER}/${REPO}.git"
  UPSTREAM_URL="${GIT_AT_GITHUB}:${UPSTREAM}/${REPO}.git"

  # Make fork if needed
  if ! "${GH}" repo list | grep "^${GHUSER}/${REPO}" > /dev/null; then
    _gh repo fork --clone=false "${UPSTREAM}/${REPO}"
  fi
  # Get the data from N months ago.  This is more useful than specifying
  # --depth=3, and provides a big enough window that a user who is
  # restoring a deleted gee repository is unlikely to have issues.
  local OLDEST_COMMIT
  OLDEST_COMMIT="$(date --date="-${CLONE_DEPTH_MONTHS} months" +"%Y-%m-%d")"
  _info "Fetching all commits since ${OLDEST_COMMIT}"
  _git clone \
    --shallow-since "${OLDEST_COMMIT}" \
    --no-single-branch \
    "${URL}" \
    "${REPO_DIR}/${MAIN}"
  cd "${REPO_DIR}/${MAIN}"
  _git remote add upstream "${UPSTREAM_URL}"
  _git fetch upstream
  _git remote -v

  # Fix the name of the main branch to match the actual branch name:
  local OLD_MAIN="${MAIN}"
  unset MAIN
  _set_main_by_asking_github
  cd "${REPO_DIR}"
  mv "${OLD_MAIN}" "${MAIN}"
  cd "${MAIN}"

  _info "Created ${REPO_DIR}/${MAIN}"

  # by default, enable meld (guitool) and vimdiff (mergetool):
  gee__config default
}

##########################################################################
# config command
##########################################################################

_register_help "config" \
  "Set various configuration options." <<'EOT'
Usage: gee config <option>

Valid configuration options are:

* "default": Reset to default settings.
* "enable_vim": Set "vimdiff" as your merge tool.
* "enable_emacs": Set "emacs" as your merge tool.
* "enable_vscode": Set "vscode" as your GUI merge tool.
* "enable_meld": Set "meld" as your GUI merge tool.
EOT
def gee__config():
  case "$1" in
    default)
      gee__config enable_vim
      gee__config enable_meld
      ;;
    enable_vim)
      _info "Setting vimdiff as the default text-based diff and merge tool."
      _git config --global merge.tool vimdiff
      _git config --global merge.conflictstyle diff3
      _git config --global mergetool.prompt false
      _git config --global diff.difftool vimdiff
      _git config --global difftool.vimdiff.cmd \
        "vimdiff \"\$LOCAL\" \"\$REMOTE\""
      ;;
    enable_vscode)
      _info "Setting vscode as the default GUI diff and merge tool."
      _git config --global merge.guitool vscode
      _git config --global mergetool.vscode.cmd \
         "code --wait \$MERGED"
      ;;
    enable_meld)
      _info "Setting meld as the default GUI diff and merge tool."
      _git config --global merge.guitool meld
      _git config --global mergetool.meld.cmd \
        "/usr/bin/meld \"\$LOCAL\" \"\$MERGED\" \"\$REMOTE\" --output \"\$MERGED\""
      _git config --global diff.guitool meld
      _git config --global difftool.meld.cmd \
        "/usr/bin/meld \"\$LOCAL\" \"\$REMOTE\""
      ;;
    enable_emacs)
      # TODO: I'm hoping an emacs expert can contribute
      # something here.
      _fatal "enable_emacs: not yet supported."
      ;;
    *)
      _info "Valid options include:" \ default \ enable_vim \ enable_emacs \ enable_vscode
      _fatal "Unknown configuration directive."
      ;;
  esac
}

##########################################################################
# make_branch command
##########################################################################

_register_help "make_branch" \
  "Create a new child branch based on the current branch." \
  "mkbr" \
  <<'EOT'
Usage: gee make_branch <branch-name> [<commit-ish>]
Aliases: mkbr

Create a new branch based on the current branch.  The new branch will be located in the
directory:
  ~/gee/<repo>/<branch-name>

If <commit-ish> is provided, sets the HEAD of the newly created branch to that
revision.
EOT

def gee__mkbr(): gee__make_branch "$@"; }
def gee__make_branch():
  _startup_checks

  local BRNAME SHA CURRENT_BRANCH
  # TODO(jonathan): Let the user name the branch to branch from.
  BRNAME="$1"; shift
  SHA="$1"

  _set_main
  CURRENT_BRANCH="$(_get_current_branch)"
  if [[ -z "${CURRENT_BRANCH}" ]]; then
    CURRENT_BRANCH="${MAIN}"
  fi
  _checkout_or_die "${CURRENT_BRANCH}"

  local -a ARGS=( worktree add "${REPO_DIR}/${BRNAME}" )
  _git "${ARGS[@]}"
  _info "Created ${REPO_DIR}/${BRNAME}"

  _read_parents_file
  PARENTS["${BRNAME}"]="${CURRENT_BRANCH}"
  _write_parents_file

  if [[ -n "${SHA}" ]]; then
    _info "Setting HEAD of branch \"${BRNAME}\" to \"${SHA}\""
    _checkout_or_die "${BRNAME}"
    git reset --hard "${SHA}"
  fi

  # If the user has created a branch whose name matches an
  # existing branch in their existing repo, pull those changes
  # into this branch.
  if _remote_branch_exists origin "${BRNAME}"; then
    _checkout_or_die "${BRNAME}"
    _git pull --rebase origin "${BRNAME}"
    _info "Pulled in changes from origin/${BRNAME}"
  fi
}

##########################################################################
# log command
##########################################################################

_register_help "log" \
  "Log of commits since parent branch." <<'EOT'
Usage: gee log
EOT

def gee__log():
  _startup_checks

  _check_cwd
  local -a ARGS=( "$@" )
  local -a RANGE=()
  _egrep_array RANGE '^\w*\.\.\.\w*$' "$@"
  if (( ${#RANGE[@]} == 0 )); then
    local PARENT_BRANCH CURRENT_BRANCH
    _read_parents_file
    PARENT_BRANCH="$(_get_parent_branch)"
    CURRENT_BRANCH="$(_get_current_branch)"
    ARGS+=("${PARENT_BRANCH}...${CURRENT_BRANCH}")
  fi
  local -a PRETTYLOG=(
      log
      --color
      --graph
      "--pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%cr) %C(bold blue)<%an>%Creset'"
      --abbrev-commit
  )
  _set_alias_if_missing logp "${PRETTYLOG[*]}"
  _git logp "${ARGS[@]}"
}

##########################################################################
# diff command
##########################################################################

_register_help "diff" \
  "Differences in this branch." <<'EOT'
Usage: gee diff [<files...>]

Diffs the current branch against its parent branch.

If <files...> are omited, defaults to all files.
EOT

def gee__diff():
  _startup_checks

  _check_cwd
  local PARENT_BRANCH
  _read_parents_file
  PARENT_BRANCH="$(_get_parent_branch)"
  # branches created with "gee pr_checkout" need special handling:
  if [[ "${PARENT_BRANCH}" == upstream/* ]]; then
    _git fetch upstream "${PARENT_BRANCH#upstream/}"
    PARENT_BRANCH="FETCH_HEAD"
  fi
  if (( "$#" )); then
    _git_can_fail diff "${PARENT_BRANCH}" -- "$@"
  else
    _git_can_fail diff "${PARENT_BRANCH}"
  fi
  if _branch_has_unstaged_changes; then
    _info "Note: This branch contains uncommitted changes."
  fi
}

##########################################################################
# pack command
##########################################################################

_register_help "pack" \
  "Exports all unsubmitted changes in this branch as a pack file." <<'EOT'
Usage: gee pack [-c] [-o <file.pack>]

Creates a pack file containing all unsubmitted changes in this branch.

Flags:
  -o  Specifies a file to write to, instead of stdout.

EOT

def gee__pack():
  _startup_checks

  _check_cwd
  _set_main
  declare -A FLAGS=()
  _parse_options "o:" "$@"
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  if _branch_has_unstaged_changes; then
    _warn "You have uncommitted work in this branch that won't be packed."
    _confirm_or_exit "Do you want to proceed anyway? (y/N)  "
  fi
  local HEAD_COMMIT
  HEAD_COMMIT="$("${GIT}" rev-parse HEAD)"
  local -a OPTS=( "--inter-hunk-context=3" )
  local OUTPUT
  OUTPUT="${FLAGS[o]}"
  if [[ -n "${OUTPUT}" ]]; then
    _info "Writing to ${OUTPUT}"
    exec >"${OUTPUT}"
  else
    _info "Writing to stdout"
  fi
  (
    echo "PACKFILE v1.0"
    echo "User: $(whoami)"
    echo "Date: $(date)"
    echo "Branch: ${CURRENT_BRANCH}"
    echo "Head: ${HEAD_COMMIT}"
    echo ""
    echo "git diff ${MAIN}...${CURRENT_BRANCH}"
    echo "### start of patch"
    "${GIT}" diff "${OPTS[@]}" "${MAIN}...${CURRENT_BRANCH}"
    echo "### end of patch"
  ) </dev/null
}

##########################################################################
# unpack command
##########################################################################

_register_help "unpack" \
  "Patch the local branch from a pack file." <<'EOT'
Usage: gee unpack <file.pack>

"unpack" attempts to patch the current branch from a pack file.
EOT

def gee__unpack():
  _startup_checks

  _check_cwd
  _set_main
  local PACKFILE
  PACKFILE="$1"
  if [[ -z "${PACKFILE}" ]]; then
    _fatal "unpack: you must specify a pack file to read."
  fi
  if ! [[ -f "${PACKFILE}" ]]; then
    _fatal "unpack: Could not find \"${PACKFILE}\""
  fi
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  local STATUS
  STATUS="$("${GIT}" status --porcelain)"
  if [[ -n "${STATUS}" ]]; then
    _warn "You have uncommitted work in this branch that could be disrupted."
    _confirm_or_exit "Do you want to proceed anyway? (y/N)  "
  fi
  cd "$(_get_branch_rootdir "${CURRENT_BRANCH}")"
  local -a HEADER=()
  local LINE
  while IFS= read -r LINE; do
    if [[ -z "${LINE}" ]]; then
      break
    fi
    HEADER+=( "${LINE}" )
  done <"${PACKFILE}"
  _info "Applying ${PACKFILE}:" "${HEADER[@]}" ""
  patch -p1 <"${PACKFILE}"
}


## DEPRECATED: we always squash when submitting a PR, this command adds a lot
## of complexity but very little value.
## TODO(jonathan): delete this after we are sure we don't need it.
## ##########################################################################
## # squash command
## ##########################################################################
##
## _register_help "squash" "Squash all commits in this branch." <<'EOT'
## Usage: gee squash
##
## Squash all commits in this branch to a single commit.
## EOT
##
## def gee__squash():
##  _startup_checks
##
##   _check_cwd
##   # Note: there is no longer any need to squash before sending out for review,
##   # as we always merge with "gh pr merge --squash".
##   # TODO(jonathan): Deprecate this?
##   local PARENT_BRANCH CURRENT_BRANCH MERGE_BASE TMPFILE NUM_COMMITS
##   _read_parents_file
##   PARENT_BRANCH="$(_get_parent_branch)"
##   CURRENT_BRANCH="$(_get_current_branch)"
##   MERGE_BASE="$("${GIT}" merge-base "${PARENT_BRANCH}" "${CURRENT_BRANCH}")"
##   NUM_COMMITS="$("${GIT}" log --oneline "${PARENT_BRANCH}..${CURRENT_BRANCH}" | /usr/bin/wc -l)"
##   _info "Current branch: ${CURRENT_BRANCH}"
##   _info "Parent branch: ${PARENT_BRANCH}"
##   _info "Merge base: ${MERGE_BASE}"
##   _info "Number of commits outstanding: ${NUM_COMMITS}"
##   if [[ "${NUM_COMMITS}" -lt 2 ]]; then
##     _fatal "${NUM_COMMITS} commits outstanding.  Nothing to squash."
##   fi
##   TMPFILE="$(mktemp)"
##   (
##     echo "# Comment lines will be removed."
##     echo "# One line summary:"
##     echo "[REPLACE THIS LINE]"
##     echo ""
##     echo "# Details:"
##     echo ""
##     echo "Files:"
##     "${GIT}" diff --name-only "${MERGE_BASE}" | sed 's/^/    /'
##     echo ""
##     echo "# Previous commit messages:"
##     echo "#"
##     "${GIT}" log --format=medium "${PARENT_BRANCH}..${CURRENT_BRANCH}" | \
##       sed 's/^/# /'
##     echo ""
##   ) >"${TMPFILE}"
##   "${EDITOR:-vim}" "${TMPFILE}"
##   sed -i "/^#/d" "${TMPFILE}"
##   perl -p -i -e 's/(^\s*\n){2,}/\n/gmso' "${TMPFILE}"
##   if grep -q "^\[REPLACE" "${TMPFILE}"; then
##     echo "You must replace the summary line."
##     echo "Commit message saved here: ${TMPFILE}"
##     _fatal Aborted.
##   fi
##   head -n 1 "${TMPFILE}"
##   _confirm_or_exit \
##     "Are you sure you want to squash ${NUM_COMMITS} commits? (y/N)  "
##
##   _git reset --soft "${MERGE_BASE}"
##   _git add --all
##   _git commit -a -F "${TMPFILE}"
##   rm "${TMPFILE}"
##   _push_to_origin "+${CURRENT_BRANCH}"  # update remote branch too
##   _git log --oneline "${PARENT_BRANCH}..${CURRENT_BRANCH}"
## }

##########################################################################
# update command
##########################################################################

_register_help "update" \
  "integrate changes from parent into this branch." \
  "up" \
  <<'EOT'
Usage: gee update

"gee update" attempts to rebase this branch atop its parent branch.

If a merge conflict occurs, "gee" drops the user into a sub-shell where the
user can either resolve the conflicts and continue, or abort the entire
operation.
EOT

def gee__up(): gee__update "$@"; }
def gee__update():
  _startup_checks

  _check_cwd
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  if [[ -z "${CURRENT_BRANCH}" ]]; then
    _die "Not in a git branch directory."
  fi

  # Check for upstream changes in "origin" first:
  if _remote_branch_exists origin "${CURRENT_BRANCH}"; then
    _git fetch origin
    local -a COUNTS
    read -r -a COUNTS < <("${GIT}" rev-list --left-right --count "${CURRENT_BRANCH}...origin/${CURRENT_BRANCH}")
    if [[ "${COUNTS[1]}" -gt 0 ]]; then
      _warn "Remote branch origin/${CURRENT_BRANCH} is ${COUNTS[1]} commit(s) ahead of ${CURRENT_BRANCH}."
      _info \
        "There are two likely causes.  Either:" \
        "" \
        "* If you or another user pushed commits into this branch from another branch" \
        "  or machine, in which case you probably want to integrate these changes, or" \
        "" \
        "* If you manually rebased your local branch, but forgot to force-push the" \
        "  updated branch to origin, in which case you should do a force-push instead" \
        "  of integrating."
      if _confirm_default_yes "Do you want to integrate changes from origin/${CURRENT_BRANCH}? (Y/n)  "; then
        _info "Pulling in changes from origin/${CURRENT_BRANCH}"
        HEAD="$("${GIT}" rev-parse HEAD)"
        _info "Old head commit before rebase: ${HEAD}"
        _git rebase --no-autostash "origin/${CURRENT_BRANCH}"
      else
        _info "You may need to \"git push -u origin --force\" to fix your origin remote."
      fi
    fi
  fi

  _read_parents_file

  local PREVIOUS_B
  PREVIOUS_B="$(_get_parent_branch "${CURRENT_BRANCH}")"

  _set_main
  if [[ "${PREVIOUS_B}" == "${MAIN}" ]]; then
    _update_main
  fi

  _checkout_or_die "${CURRENT_BRANCH}"
  # Rebase from PREVIOUS_B onto B
  _safer_rebase "${CURRENT_BRANCH}" "${PREVIOUS_B}"
  _info Done.
}

##########################################################################
# rupdate command
##########################################################################

_register_help "rupdate" \
  "Recursively integrate changes from parents into this branch." \
  "rup" <<'EOT'
Usage: gee rupdate

"gee rupdate" recursively rebases each branch onto it's parent.

If a merge conflict occurs, "gee" drops the user into a sub-shell where the
user can either resolve the conflicts and continue, or abort the entire
operation.  Note that the merge conflict can be in a parent of the current
branch.
EOT

def _add_parent_branches_to_chain():
  # Recursively finds all parent branches of B, and inserts them
  # into CHAIN unless they are already in CHAIN.  Ultimately,
  # the branches in CHAIN will be strictly ordered so that
  # any parent is earlier than any child.  Recursive searching
  # for parent branches ultimately stops when we reach an
  # upstream branch.
  local B="$1"
  if _contains_element "${B}" "${CHAIN[@]}"; then return; fi
  if [[ "${B}" == "upstream/"* ]]; then return; fi
  _add_parent_branches_to_chain "$(_get_parent_branch "${B}")"
  if (( "${#CHAIN[@]}" > 500 )); then
    _info "CHAIN: ${CHAIN[*]}"
    _die "_add_parent_branches_to_chain recursed too deeply."
  fi
  CHAIN+=( "${B}" )
}

def gee__rup(): gee__rupdate "$@"; }
def gee__rupdate():
  _startup_checks

  _check_cwd
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  if [[ -z "${CURRENT_BRANCH}" ]]; then
    _die "Not in a git branch directory."
  fi

  _read_parents_file

  # Build a chain of branches to update.
  _set_main
  local -a CHAIN=()
  _add_parent_branches_to_chain "${CURRENT_BRANCH}"  # puts branches into CHAIN array

  for B in "${CHAIN[@]}"; do
    local PARENT_BRANCH
    PARENT_BRANCH="$(_get_parent_branch "${B}")"
    _banner "Updating branch \"${B}\" from \"${PARENT_BRANCH}\""
    _checkout_or_die "${B}"

    # Check if we're rebasing onto a branch with uncommitted changes:
    if [[ "${B}" != "${CURRENT_BRANCH}" ]]; then
      local -a UNCOMMITTED
      mapfile -t UNCOMMITTED < <( "${GIT}" status --short -uall )
      if (( "${#UNCOMMITTED[@]}" )); then
        _fatal "Branch ${B} contains ${#UNCOMMITTED[@]} uncommitted changes:" \
          "${UNCOMMITTED[@]}" \
          "Commit branch ${B} and try again."
      fi
    fi

    # Rebase from PREVIOUS_B onto B
    _safer_rebase "${B}" "${PARENT_BRANCH}"
  done
  _info Done.
}

##########################################################################
# update_all command
##########################################################################

_register_help "update_all" \
  "Update all branches."  \
  "up_all" <<'EOT'
Usage: gee update_all

"gee update_all" updates all local branches (in the correct order),
by rebasing child branches onto parent branches.

If a merge conflict occurs, "gee" drops the user into a sub-shell where the
user can either resolve the conflicts and continue, or abort the entire
operation.  Note that the merge conflict can be in a parent of the current
branch.
EOT

def gee__up_all(): gee__update_all "$@"; }
def gee__update_all():
  _startup_checks

  local -a CONFLICTS=()
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  if [[ -z "${CURRENT_BRANCH}" ]]; then
    _set_main
    CURRENT_BRANCH="${MAIN}"
    _checkout_or_die "${CURRENT_BRANCH}"
  fi

  _read_parents_file

  # Build an ordered list of branches to update
  local -a ALL_BRANCHES=()
  mapfile -t ALL_BRANCHES < <( "${GIT}" branch --format="%(refname:short)" )
  local -a CHAIN=()
  local B
  for B in "${ALL_BRANCHES[@]}"; do
    _add_parent_branches_to_chain "${B}"
  done
  _info "Updating ${CHAIN[*]}"

  local PARENT
  for B in "${CHAIN[@]}"; do
    _banner "Updating branch \"${B}\""
    _checkout_or_die "${B}"

    # Check if we're rebasing onto a branch with uncommitted changes:
    local -a UNCOMMITTED
    mapfile -t UNCOMMITTED < <( "${GIT}" status --short -uall )
    if (( "${#UNCOMMITTED[@]}" )); then
      _warn "Branch ${B} contains ${#UNCOMMITTED[@]} uncommitted changes:" \
        "${UNCOMMITTED[@]}" \
        "This branch will not be updated."
      continue
    fi

    # Rebase from PREVIOUS_B onto B
    PARENT="$(_get_parent_branch "${B}")"
    if ! _safer_rebase "${B}" "${PARENT}"; then
      CONFLICTS+=( "${B}" )
    fi
  done
  if (( "${#CONFLICTS[@]}" )); then
    _fatal "The following branches had merge conflicts:" "${CONFLICTS[@]}"
  fi
  _info Done.
}

##########################################################################
# whatsout command
##########################################################################

_register_help "whatsout" \
  "List locally changed files in this branch." <<'EOT'
Usage: gee whatsout

Reports which files in this branch differ from parent branch.
EOT

def gee__whatsout():
  _startup_checks

  _check_cwd
  local BRANCH PARENT
  BRANCH="$(_get_current_branch)"
  _read_parents_file
  PARENT="$(_get_parent_branch "${BRANCH}")"
  _git diff --name-only "${PARENT}...${BRANCH}"
  if _branch_has_unstaged_changes; then
    _info "Note: This branch contains uncommitted changes."
  fi
}

##########################################################################
# codeowners command
##########################################################################

_register_help "codeowners" \
  "Provide detailed information about required approvals for this PR." \
  owners reviewers \
  <<'EOT'
Usage: gee codeowners [--comment]

Gee examines the set of modified files in this branch, and compares it against
the rules in the CODEOWNERS file.  Gee then presents the user with detailed
information about which approvals are necessary to submit this PR:

*  Each line contains a list of users who are code owners of at least
   some part of the PR.

*  A minimum of one user from each line must provide approval in order for the
   PR to be merged.

If `--comment` option is specified, the codeowners information is appended to the
current PR as a new comment.

EOT

def gee__owners(): gee__codeowners "$@"; }
def gee__reviewers(): gee__codeowners "$@"; }
def gee__codeowners():
  _startup_checks

  local OPT_COMMENT=0
  if [[ "$1" == "--comment" ]]; then
    OPT_COMMENT=1
  fi

  local COMMENT
  read -r -d "" COMMENT < <(
    # add a second comment containing the CODEOWNERS annotation
    local OWNERS=()
    mapfile -t OWNERS < <(_print_codeowners)
    if [[ "${#OWNERS[@]}" -eq 0 ]]; then
      printf "This PR can be submitted with approval from any repo owner.\n\n"
    elif [[ "${#OWNERS[@]}" -eq 1 ]]; then
      printf "This PR requires approval from at least one of these reviewers:\n\n"
      printf "* %s\n" "${OWNERS[@]}"
    else
      printf "This PR requires approval from at least one reviewer from each line:\n\n"
      printf "* %s\n" "${OWNERS[@]}"
    fi
    printf "\n"
  ) || /bin/true

  _info "${COMMENT}"
  if (( OPT_COMMENT )); then
    _gh pr comment --body "${COMMENT}"
  fi
}

##########################################################################
# lsbranches command
##########################################################################
# TODO(jonathan): maybe display a tree?
# TODO(jonathan): maybe display this suggestion from Jacob Adelmann:
# branchp = !git for-each-ref \
#   --sort=committerdate refs/heads/ \
#   --format='%(HEAD) %(color:yellow)%(refname:short)%(color:reset) - %(color:red)%(objectname:short)%(color:reset) - %(contents:subject) - %(authorname) (%(color:green)%(committerdate:relative)%(color:reset))'

_register_help "lsbranches" \
  "List information about each branch." \
  "lsb" "lsbr" <<'EOT'
Usage: gee lsbranches

List information about all branches.

NOTE: the output of this command is likely to change in the near future.
EOT

def gee__lsb(): gee__lsbranches "$@"; }
def gee__lsbr(): gee__lsbranches "$@"; }
def gee__lsbranches():
  _startup_checks

  _set_main
  if ! _in_gee_repo; then
    cd "${REPO_DIR}"
  fi
  if ! _in_gee_branch; then
    cd "${REPO_DIR}/${MAIN}"
  fi

  local -a branches;
  mapfile -t branches < <( "${GIT}" branch --format "%(refname:short)")
  local br
  _set_main
  _git fetch
  for br in "${branches[@]}"; do
    if [[ "${br}" != "${MAIN}" ]]; then
      _branch_ahead_behind "${br}"
    fi
  done
}

##########################################################################
# cleanup command
##########################################################################
_register_help "cleanup" \
  "Automatically remove branches without local changes." <<'EOT'
Usage: gee cleanup

Automatically removes branches without local changes.
EOT

def gee__cleanup():
  _startup_checks

  _set_main
  _read_parents_file

  if ! _in_gee_repo; then
    cd "${REPO_DIR}"
  fi
  if ! _in_gee_branch; then
    cd "${REPO_DIR}/${MAIN}"
  fi

  local -a branches;
  mapfile -t branches < <( "${GIT}" branch --format "%(refname:short)")

  local -a empty=()
  local br
  for br in "${branches[@]}"; do
    if [[ "${br}" != "${MAIN}" ]]; then
      local parent
      parent="$(_get_parent_branch "${br}")"
      if [[ "${parent}" == upstream/* ]]; then
        _git fetch upstream "${parent#upstream/}"
        parent="FETCH_HEAD"
      fi
      local -a  counts=()
      read -r -a counts < <("${GIT}" rev-list --left-right --count "${parent}...${br}")
      _checkout_or_die "${br}"
      if [[ -z "$("${GIT}" status --porcelain)" ]]; then
        if (( counts[1] == 0 )); then
          empty+=( "${br}" )
        fi
      fi
    fi
  done

  if [[ "${#empty[@]}" -eq 0 ]]; then
    _info "Nothing to clean up: all branches contain local changes."
    return 0
  fi

  # use "dialog" if it's available in this container:
  if command -v dialog > /dev/null; then
    _gee_cleanup_with_dialog "${empty[@]}"
  else
    _gee_cleanup_without_dialog "${empty[@]}"
  fi
}

def _gee_cleanup_with_dialog():
  local -a DIALOG_OPTS=(
    --no-lines
    --keep-tite
    --backtitle "gee ${VERSION}"
    --title "gee cleanup"
    --checklist
    "The following branches have no local changes.  Select the branches that you want to delete:"
    0 0 0
  )
  local BRANCH
  for BRANCH in "${empty[@]}"; do
    DIALOG_OPTS+=( "${BRANCH}" "" "off" )
  done

  # Invoke dialog:
  local RESULT EXITCODE
  set +e
  exec 3>&1
  RESULT="$(dialog "${DIALOG_OPTS[@]}" 2>&1 1>&3)"
  EXITCODE=$?
  exec 3>&-
  set -e
  if (( EXITCODE != 0 )); then
    # The user selected "cancel" instead of "ok"
    _warn "Cancelled cleanup operation."
    return
  fi

  # remove selected branches:
  local -a SELECTED=()
  read -r -a SELECTED <<< "${RESULT}"
  _info "Removing: ${SELECTED[*]}"
  for BRANCH in "${SELECTED[@]}"; do
    gee__rmbr "${BRANCH}"
  done
}

def _gee_cleanup_without_dialog():
  # The dialog utility isn't installed, so we ask the user a series of
  # yes/no questions instead.
  local -a empty=( "$@" )
  _info "The following branches have no local changes:"
  _info "  ${empty[*]}"
  _checkout_or_die "${MAIN}"
  for br in "${empty[@]}"; do
    if _confirm_default_no "Do you want to remove the \"${br}\" branch? (y/N)  "; then
      gee__rmbr "${br}"
    fi
  done
}

##########################################################################
# get_parent command
##########################################################################

_register_help "get_parent" \
  "Which branch is this branch branched from?" <<'EOT'
Usage: gee get_parent
EOT

def gee__get_parent():
  _startup_checks

  _check_cwd
  local BRANCH
  BRANCH="${1:-$(_get_current_branch)}"
  _read_parents_file
  PARENT="$(_get_parent_branch "${BRANCH}")"
  echo "${PARENT} is the parent branch of ${BRANCH}"
}

##########################################################################
# set_parent command
##########################################################################

_register_help "set_parent" \
  "Set another branch as parent of this branch." <<'EOT'
Usage: gee set_parent <parent-branch>

Gee keeps track of which branch each branch is branched from.  You can
change the parent of the current branch with this command, but be sure
to do a "gee update" operation afterwards.
EOT

def gee__set_parent():
  _startup_checks

  _check_cwd
  local BRANCH PARENT
  BRANCH="$(_get_current_branch)"
  PARENT="$1"
  if [[ -z "${PARENT}" ]]; then
    _fatal "Must specify a parent branch."
  fi
  _read_parents_file
  PARENTS["${BRANCH}"]="$1"
  _write_parents_file
  echo "${PARENT} is the parent branch of ${BRANCH}"
}

##########################################################################
# commit command
##########################################################################

_register_help "commit" \
  "Commit all changes in this branch" \
  "push" "c" <<'EOT'
Usage: gee commit [<git commit options>]

Commits all outstanding changes to your local branch, and then immediately
pushes your commits to `origin` (your private, remote github repository).

"commit" can be used to checkpoint and back up work in progress.

Note that if you are working in a PR-associated branch created with `gee
pr_checkout`, your commits will be pushed to your `origin` remote, and the
remote PR branch.  To contribute your changes back to another user's PR branch,
use the `gee pr_push` command.

Example:

    gee commit -m "Added \"gee commit\" command."

See also:

* pr_push

EOT

def gee__push(): gee__commit "$@"; }
def gee__c(): gee__commit "$@"; }
def gee__commit():
  _startup_checks

  _check_cwd
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  _set_main
  if [[ "${CURRENT_BRANCH}" == "${MAIN}" ]]; then
    _info "gee's workflow doesn't allow changes to the ${MAIN} branch."
    _info "You should move your changes to another branch.  For example:"
    _info "  git add -a; git stash; gee mkbr new1; gcd new1; git stash apply"
    _fatal "Commit to ${MAIN} branch denied."
  fi
  BRANCH_DIR="$(_get_branch_rootdir "${CURRENT_BRANCH}")"
  _debug "${BRANCH_DIR}" "${HOME}"
  if [[ "${BRANCH_DIR}" != "${HOME}" ]]; then
    _git add --all
  else
    echo "Skipped \"git add --all\" because branch is home dir."
  fi
  if _git_can_fail commit "$@"; then
    if _remote_branch_exists origin "${CURRENT_BRANCH}"; then
      _git fetch
      local -a COUNTS
      read -r -a COUNTS < <("${GIT}" rev-list --left-right --count "${CURRENT_BRANCH}...origin/${CURRENT_BRANCH}")
      if [[ "${COUNTS[1]}" -gt 0 ]]; then
        _warn "Remote branch origin/${CURRENT_BRANCH} is ${COUNTS[1]} commit(s) ahead of ${CURRENT_BRANCH}."
        _warn "You must integrate upstream changes before pushing."
        if _confirm_default_yes "Do you want to pull upstream changes now? (Y/n)  "; then
          _git rebase --autostash "origin/${CURRENT_BRANCH}"
        else
          _warn "Skipping backup of branch to origin/${CURRENT_BRANCH}"
          return 0
        fi
      fi
    fi
    # We always push upstream so that users have a backup in case they lose their
    # laptop:
    _push_to_origin "${CURRENT_BRANCH}"
  fi
}

##########################################################################
# revert command
##########################################################################

_register_help "revert" \
  "Revert specified files to match the parent branch." \
  <<'EOT'
Usage: gee revert <files...>

Reverts changes to the specified files, so that they become identical to the
version in the parent branch.  If the file doesn't exist in the parent
branch, it is deleted from the current branch.

Example:

    gee revert foobar.txt
EOT

def gee__revert():
  _startup_checks

  _check_cwd
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  CURRENT_ROOT="$(_get_branch_rootdir "${CURRENT_BRANCH}")"
  local PARENT_BRANCH
  _set_main
  PARENT_BRANCH="$(_get_parent_branch "${CURRENT_BRANCH}")"

  local -a MODIFIED=()
  local -a ADDED=()
  local F REL_F
  for F in "$@"; do
    REL_F="$(realpath --relative-base="${CURRENT_ROOT}" -e ${F})"
    if git cat-file blob "${PARENT_BRANCH}:${REL_F}" >/dev/null 2>&1; then
      MODIFIED+=("${F}")
    else
      ADDED+=("${F}")
    fi
  done
  if [[ "${#MODIFIED[@]}" -ne 0 ]]; then
    _git checkout "${PARENT_BRANCH}" -- "${MODIFIED[@]}"
  fi
  if [[ "${#ADDED[@]}" -ne 0 ]]; then
    _git rm "${ADDED[@]}"
  fi
}

##########################################################################
# pr_checkout command
##########################################################################
_register_help "pr_checkout" \
  "Create a client containing someone's pull request." <<'EOT'
Usage: gee pr_checkout <PR>

Creates a new branch containing the specified pull request.

Note that the new will be configured so that `gee update` will update that
branch by integrating changes from the original pull request.  However,
`gee commit` will still only push commits to your own local and `origin`
repositories.  If you want to push commits back into the original PR,
use the `pr_push` command.

See also:

* commit
* pr_push

EOT

def gee__pr_checkout():
  _startup_checks

  _check_gh_auth
  _set_main
  local PRNUM="$1"; shift
  local BRANCH="pr_${PRNUM}"

  _checkout_or_die "${MAIN}"  # or _local_branch_exists won't work.
  if ! _local_branch_exists "${BRANCH}"; then
    gee__mkbr "${BRANCH}"
  else
    _confirm_or_exit "Branch ${BRANCH} exists: okay to reset it?  "
  fi
  _checkout_or_die "${BRANCH}"

  _gh pr checkout --force --branch "${BRANCH}" "${PRNUM}"

  _read_parents_file
  PARENTS["${BRANCH}"]="upstream/refs/pull/${PRNUM}/head"
  _write_parents_file

  _checkout_or_die "${BRANCH}"
  _push_to_origin "${BRANCH}"
  _info "Pulled PR #${PRNUM} into branch \"${BRANCH}\""
}

##########################################################################
# pr_push command
##########################################################################
_register_help "pr_push" \
  "Push commits into another user's PR branch." <<'EOT'
Usage: gee pr_push

`gee pr_push` must be executed from within a branch created by `gee pr_checkout`.

`gee commit` will create a local commit, and push that commit to `origin`, the
remote repository owned by you.  `gee pr_push` can then be used to also push
your commits into another user's remote pull request branch.

`gee pr_push` will refuse to proceed unless all changes from the remote pull
request branch are already integrated in your local branch, so you might need
to `gee update` before `gee pr_push`.

After pushing your changes into another user's PR branch, be sure to directly
notify that user, so they know to pull your changes into their local branch.
Otherwise, the other user might accidentally lose your commits entirely if they
force-push.  Remote users can integrate your changes using the `gee update`
command, or `git rebase --autostash origin/<branch>` if they aren't a gee user.

See also:

* commit
* pr_checkout

EOT

def gee__pr_push():
  # Check that we are in a PR branch (a branch created with pr_checkout).  This
  # is somewhat inflexible, but since "pushing to another user's branch" is a
  # bit of a risky activity, we should be extra careful here and only support
  # one well-tested way of doing things.
  local BRANCH
  BRANCH="$(_get_current_branch)"
  _read_parents_file
  local PRNUM=""
  if [[ "${PARENTS[${BRANCH}]}" == upstream/refs/pull/*/head ]]; then
    PRNUM="$(cut -d '/' -f4 <<< "${PARENTS[${BRANCH}]}")"
  else
    _fatal "This branch does not appear to have been made with \"gee pr_checkout\"."
  fi
  _info "This branch appears to be associated with PR #${PRNUM}."

  # Identify remote branch
  local -a DATA=()
  _read_cmd DATA "${GH}" pr view "${PRNUM}" \
    --json author,headRefName,state \
    --jq '.author["login"],.headRefName,.state'
  local REMOTE_USER REMOTE_BRANCH REMOTE_STATE
  REMOTE_USER="${DATA[0]}"
  REMOTE_BRANCH="${DATA[1]}"
  REMOTE_STATE="${DATA[2]}"

  # Check that the remote branch hasn't already been merged.
  if [[ "${REMOTE_STATE}" == "MERGED" ]]; then
    _fatal "PR${PRNUM} has already been merged, refusing to proceed."
  fi

  # Fetch from remote branch.
  if ! "${GIT}" remote get-url "${REMOTE_USER}" >/dev/null 2>&1; then
    _git remote add "${REMOTE_USER}" "${GIT_AT_GITHUB}:${REMOTE_USER}/${REPO}.git" >&2
  fi
  _git fetch "${REMOTE_USER}" "${REMOTE_BRANCH}"

  # Check if we are behind remote branch
  local -a COUNTS
  read -r -a COUNTS < <("${GIT}" rev-list --left-right --count "${BRANCH}...FETCH_HEAD")
  _info "Local has ${COUNTS[0]} more commit(s) than remote ${REMOTE_USER}/${REMOTE_BRANCH}."
  _info "Remote has ${COUNTS[1]} more commit(s) than local."
  if [[ "${COUNTS[0]}" -eq 0 ]]; then
    _fatal "No local commits to push.  Perhaps you need to \"gee commit\" first?"
  fi
  if [[ "${COUNTS[1]}" -gt 0 ]]; then
    _warn "Remote branch ${REMOTE_USER}/${REMOTE_BRANCH} is ${COUNTS[1]} commit(s) ahead of ${BRANCH}."
    _fatal "You must run \"gee update\" and then try again."
  fi

  # Push our changes to the remote branch
  _git push "${REMOTE_USER}" "HEAD:${REMOTE_BRANCH}"
  _info "Successfully pushed to PR#${PRNUM} (${REMOTE_USER}/${REMOTE_BRANCH})."

  _warn "Please notify ${REMOTE_USER} that they should pull from their remote branch, " \
        "otherwise they might accidentally force-commit over your changes."
}

##########################################################################
# pr_list command
##########################################################################
_register_help "pr_list" \
  "List outstanding PRs" \
  "lspr" "list_pr" "prls" <<'EOT'
Usage: gee pr_list [<user>]

Lists information about PRs associated with the specified user (or yourself, if
no user is specified).

Example:

    $ gee lspr jonathan-enf
    PRs associated with this branch:
    OPEN 1181 codegen tool

    Open PRs authored by jonathan-enf:
    #1205   REVIEW_REQUIRED Fix libsystemc build file error.
    #1181   REVIEW_REQUIRED codegen tool
    #1158   REVIEW_REQUIRED Added @gmp//:libgmpxx
    #1148   REVIEW_REQUIRED Added gee to enkit.config.yaml.
    #1136   REVIEW_REQUIRED Unified PtrQueue and Queue implementations.
    #1130   REVIEW_REQUIRED Owners of /poc/{sim,models}
    #1059   REVIEW_REQUIRED CSV file helper library

    PRs pending their review:
    #1200  taoliu0  2021-08-12T15:26:03Z  Added an example integrating SC

EOT
def gee__lspr(): gee__pr_list "$@"; }
def gee__list_pr(): gee__pr_list "$@"; }
def gee__prls(): gee__pr_list "$@"; }
def gee__pr_list():
  _startup_checks

  _check_gh_auth
  local WHO WHO_3RD_PERSON USER YOUR
  WHO="@me"
  WHO_3RD_PERSON="you"
  USER="${GHUSER}"
  YOUR="your"
  if [[ -n "$1" ]]; then
    WHO="$1"
    WHO_3RD_PERSON="$1"
    USER="$1"
    YOUR="their"
  fi
  if _in_gee_branch; then
    _info "PRs associated with this branch:"
    _get_pull_requests
    echo ""
  fi

  _info "Open PRs authored by ${WHO_3RD_PERSON}:"
  ( printf "PR\tbranch\treview\ttitle\n" ; \
    printf "==\t======\t======\t=====\n" ; \
   "${GH}" --repo "${UPSTREAM}/${REPO}" pr list --author "${WHO}" --state open \
    --json number,reviewDecision,headRefName,title \
    --jq '.[] | "#\(.number)\t\(.headRefName)\t\(.reviewDecision)\t\(.title)"' \
    ) | column -t -s $'\t'
  echo ""

  _info "PRs pending ${YOUR} review:"
  # TODO(jonathan): ugh why doesn't this work?
  local -a JQSCRIPT=(
    '.[]'
    ' | select( .reviewDecision == "REVIEW_REQUIRED" )'
    ' | select( .reviewRequests[]? | contains({"login":"'"${USER}"'"}))'
    ' | "#\(.number)\t\(.author.login)\t\(.createdAt)\t\(.title)"'
  )
  ( printf "PR\tauthor\tcreated\ttitle\n" ; \
    printf "==\t======\t=======\t=====\n" ; \
   "${GH}" --repo "${UPSTREAM}/${REPO}" pr list \
    --json number,author,headRefName,createdAt,state,title,reviewDecision,reviewRequests \
    --jq "${JQSCRIPT[*]}" \
    ) | column -t -s $'\t'
}

##########################################################################
# edit_pr command
##########################################################################

_register_help "pr_edit" \
  "Edit an existing pull request." \
  "edpr" "pred" "edit_pr" <<'EOT'
Usage: gee edit_pr <args>

Edit an outstanding pull request.

All arguments are passed to "gh pr edit".
EOT

def gee__edpr(): gee__pr_edit "$@"; }
def gee__pred(): gee__pr_edit "$@"; }
def gee__edit_pr(): gee__pr_edit "$@"; }
def gee__pr_edit():
  _startup_checks

  _check_cwd
  _check_gh_auth
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  local FROM_BRANCH="${GHUSER}:${CURRENT_BRANCH}"
  if [[ "${UPSTREAM}" == "${GHUSER}" ]]; then
    # gh-cli treats this as a special case for some reason.
    FROM_BRANCH="${CURRENT_BRANCH}"
  fi

  if ! _gh_pr_view > /dev/null; then
    _fatal "No pull request exists for ${GHUSER}:${CURRENT_BRANCH}."
  fi

  # The same view options also work for pr edit:
  _gh pr edit --repo "${UPSTREAM}/${REPO}" "${FROM_BRANCH}" "$@"
}

##########################################################################
# pr_debug command
#
# gh api for querying pull requests is pretty wonky, this is a temporary
# command used to get some visibility into the API.
##########################################################################

def gee__pr_debug():
  _startup_checks

  _check_cwd
  _check_gh_auth
  local -a PRS=()
  _get_pull_requests "$(_get_current_branch)"
  mapfile -t PRS < <( _list_open_pr_numbers )
  _info "Open pull requests: ${PRS[*]}"
  mapfile -t PRS < <( _list_merged_pr_numbers "")
  _info "Merged pull requests: ${PRS[*]}"
}

##########################################################################
# view_pr command
##########################################################################

_register_help "pr_view" \
  "View an existing pull request." \
  "view_pr" <<'EOT'
Usage: gee pr_view

View an outstanding pull request.
EOT

def gee__view_pr(): gee__pr_view "$@"; }
def gee__pr_view():
  _startup_checks

  _check_cwd
  _check_gh_auth
  if ! _gh_pr_view; then
    _fatal "No pull request exists for ${GHUSER}:${CURRENT_BRANCH}."
  fi
}

##########################################################################
# make_pr command
##########################################################################

_register_help "pr_make" \
  "Creates a pull request from this branch." \
  "mail" "send" "pr_create" "create_pr" "make_pr" "mkpr" "prmk" <<'EOT'
Usage: gee make_pr <gh-options>

Creates a new pull request from this branch.  The user will be asked to
edit a PR description file before the PR is created.

If you have any second thoughts during this process: Adding the token "DRAFT"
to your PR description will cause the PR to be marked as a draft.  Adding the
token "ABORT" will cause gee to abort the creation of your PR.

Uses the same options as "gh pr create".
EOT

def gee__mail(): gee__pr_make "$@"; }
def gee__send(): gee__pr_make "$@"; }
def gee__mkpr(): gee__pr_make "$@"; }
def gee__prmk(): gee__pr_make "$@"; }
def gee__make_pr(): gee__pr_make "$@"; }
def gee__pr_create(): gee__pr_make "$@"; }
def gee__create_pr(): gee__pr_make "$@"; }
def gee__pr_make():
  _startup_checks

  _check_cwd
  _check_gh_auth
  local DEST_BRANCH CURRENT_BRANCH MERGE_BASE NUM_COMMITS
  _git fetch upstream
  _read_parents_file
  _set_main
  DEST_BRANCH="upstream/${MAIN}"
  CURRENT_BRANCH="$(_get_current_branch)"
  MERGE_BASE="$("${GIT}" merge-base "${DEST_BRANCH}" "${CURRENT_BRANCH}")"
  _info "Current branch: ${CURRENT_BRANCH}"
  _info "Destination branch: ${DEST_BRANCH}"
  _info "Merge base: ${MERGE_BASE}"

  local STATE NUMBER TITLE
  read -r STATE NUMBER TITLE < \
    <( _get_pull_requests "${CURRENT_BRANCH}" | grep -v ^MERGED) \
    || /bin/true  # don't fail if empty
  if [[ "${STATE}" == "OPEN" ]]; then
    _info "Open PR exists: #${NUMBER} \"${TITLE}\"" \
          "Use \"gee commit\" to update existing PR."
    _fatal Aborted.
  elif [[ "${STATE}" == "DRAFT" ]]; then
    _info "Draft PR exists: #${NUMBER} \"${TITLE}\""
    if _confirm_default_yes "Do you want to mark this PR as ready for review? (Y/n)  "; then
      _gh pr ready "${NUMBER}" || echo $?
      exit 0
    else
      _info "Leaving PR #${NUMBER} as a draft."
      exit 0
    fi
  fi

  local UNCOMMITTED
  UNCOMMITTED="$("${GIT}" status --short -uall | wc -l)"
  if [[ "${UNCOMMITTED}" -gt 0 ]]; then
    echo "Branch contains ${UNCOMMITTED} uncommitted changes:"
    _git status --short -uall
    echo "Run \"gee commit\" and try again."
    _fatal Aborted.
  fi

  NUM_COMMITS="$("${GIT}" log --oneline "${DEST_BRANCH}..${CURRENT_BRANCH}" | wc -l)"
  if [[ "${NUM_COMMITS}" -eq 0 ]]; then
    _debug "command: ${GIT} log --oneline ${DEST_BRANCH}..${CURRENT_BRANCH}"
    _fatal "No changes in this branch."
  fi

  local FROM_BRANCH="${GHUSER}:${CURRENT_BRANCH}"
  if [[ "${UPSTREAM}" == "${GHUSER}" ]]; then
    # gh-cli treats this as a special case for some reason.
    FROM_BRANCH="${CURRENT_BRANCH}"
  fi

  local PARENT
  PARENT="$(_get_parent_branch)"
  local -a PARENT_PRS
  mapfile -t PARENT_PRS < <( _list_open_pr_numbers "$(_get_parent_branch)" )

  local BODYFILE
  BODYFILE="$(mktemp -t prbody.XXXXXX.txt)"
  (
    cat <<'EndOfPrTemplate'
# The first line should be the PR title.
# For example: "scripts/gee: Add new PR template."

# In this section, explain why the PR is necessary and what the PR desires to
# achieve.  List any relevant Jira tickets.

# In this section, describe what testing was done to validate your change.
Tested:

# Having second thoughts?  Uncomment one of these two lines:
# DRAFT   # To mark this PR as a "draft"
# ABORT   # To abort the creation of a PR.

EndOfPrTemplate

    if (( "${#PARENT_PRS[@]}" > 0 )); then
      printf "NOTE: this PR includes changes that are also in PR #%d.\n" "${PARENT_PRS[0]}"
      printf "      Review and submit that PR first, so that this PR will be\n"
      printf "      easier to read.\n\n"
    fi
    printf "\n# The following files were modified in this PR:\n"
    "${GIT}" diff --name-only "${PARENT}...${CURRENT_BRANCH}" | sed 's/^/# /' | cat
    printf "#\n# Here is your commit log:\n"
    "${GIT}" log --reverse "${PARENT}...${CURRENT_BRANCH}" | sed 's/^/# /' | cat

    # TODO(jonathan): add support for specifying tags and reviewers in the
    #                 PR description.
  ) >"${BODYFILE}"

  if [[ -n "${EDITOR}" ]]; then
    "${EDITOR}" "${BODYFILE}"
  else
    declare -g RESP=""
    _choose_one "How do you want to edit the PR description?? ([V]im, [e]macs, or [a]bort)  " \
        "vVeEaA" "v"
    case "${RESP}" in
      [vV]*) vim "${BODYFILE}"; ;;
      [eE]*) emacs "${BODYFILE}"; ;;
      [aA]*) _fatal "Aborted."; ;;
    esac
  fi

  # Remove all comments from the description and squeeze multiple blank
  # lines into single blank lines:
  sed -i '/^#/d;/\S/,/^\s*$/!d' "${BODYFILE}"

  if grep --quiet -E "^\s*ABORT" "${BODYFILE}"; then
    _fatal "Aborting creation of new PR, as requested."
    exit 1
  fi

  # Check that the PR description looks valid
  _check_pr_description "${BODYFILE}"

  # Parse metadata from PR description.
  local TITLE
  TITLE="$(head -1 "${BODYFILE}")"
  # We remove the title from the body file to avoid "stuttering" the
  # title when we eventually submit the PR:
  sed -i '1{d};2{/^$/d}' "${BODYFILE}"

  _push_to_origin "${CURRENT_BRANCH}"
  # gh pr is arcane and confusing, but this works:
  #  -R specifies the repo that we are pushing changes into.
  #  -H specifies the branch that contains outstanding commits,
  #     formatted username:branchname.
  #  -B specifies the branch we want to merge to, defaults to ${UPSTREAM}:${MAIN}
  local -a PR_ARGS
  PR_ARGS+=(
    --repo "${UPSTREAM}/${REPO}"
    --title "${TITLE}"
    -H "${GHUSER}:${CURRENT_BRANCH}"
    --body-file "${BODYFILE}"
  )

  local DRAFT=0
  if grep --quiet -E "^\s*DRAFT" "${BODYFILE}"; then
    DRAFT=1
    PR_ARGS+=( --draft )
  fi

  PR_ARGS+=( "$@" )

  _gh pr create "${PR_ARGS[@]}"

  if (( DRAFT != 1 )); then
    gee__codeowners --comment
  fi

  # _gh pr checks  # These take a while to run, no point checking now.
  _gh_pr_view

  if (( DRAFT != 1 )); then
    local NUM_REVIEWERS
    NUM_REVIEWERS="$(gh pr view \
      --repo "${UPSTREAM}/${REPO}" "${GHUSER}:${CURRENT_BRANCH}" \
      --json reviewRequests --jq '.reviewRequests | length')"
    if (( NUM_REVIEWERS == 0 )); then
      _warn "Don't forget to add reviewers!"
    fi
  fi
  rm "${BODYFILE}"
}

##########################################################################
# pr_check command
##########################################################################

_register_help "pr_check" \
  "Checks whether presubmit tests for a PR." \
  "pr_checks" "check_pr" <<'EOT'
Usage: gee pr_check

Checks presubmit tests.
EOT

def gee__check_pr(): gee__pr_check "$@"; }
def gee__pr_checks(): gee__pr_check "$@"; }
def gee__pr_check():
  _startup_checks

  local -a CHECKS=()
  local -A CHECK_COUNTS=()
  local PENDING=1
  local CHECKS_FAILED=0
  local WAIT=0
  while [[ "${PENDING}" -ne 0 ]]; do
    if _read_cmd CHECKS "${GH}" pr checks; then
      _info "${CHECKS[@]}"
      # no failures, nothing pending.
      _info "All checks successful."
      CHECKS_FAILED=0
      PENDING=0
    else
      _info "${CHECKS[@]}"
      # something went wrong.  Is a test pending, or did we get a failure?
      CHECK_COUNTS=( [fail]=0 )
      local LINE
      for LINE in "${CHECKS[@]}"; do
        local -a FIELDS=()
        read -r -a FIELDS <<< "${LINE}"
        (( CHECK_COUNTS["${FIELDS[2]}"]++ )) || /bin/true
      done
      local REPORT="gh pr checks:"
      local KEY
      for KEY in "${!CHECK_COUNTS[@]}"; do
        REPORT+="$(printf " %s=%d" "${KEY}" "${CHECK_COUNTS["${KEY}"]}")"
      done
      _info "${REPORT}"
      CHECKS_FAILED="${CHECK_COUNTS["fail"]}"
      PENDING="${CHECK_COUNTS["pending"]}"
      if [[ "${PENDING}" -ne 0 ]]; then
        if (( WAIT == 0 )); then
          declare -g RESP=""
          _choose_one "Some checks are still pending.  [W]ait, [r]etry, [a]bort, or [c]ontinue without waiting?  " \
            "rRwWaAcC" "w"
          case "${RESP}" in
            [aA]*) _fatal "Aborted."; ;;
            [cC]*) return 1; ;;
            [wW]*)
              _info "Waiting until all pending tests are complete."
              WAIT=1
              ;;
          esac
        fi
        sleep 10
      fi
    fi
  done
  if (( CHECKS_FAILED > 0 )); then
    _warn "Some presubmit checks have failed."
  fi
  return "${CHECKS_FAILED}"
}

##########################################################################
# submit_pr command
##########################################################################

_register_help "pr_submit" \
  "Merge the approved PR into the parent branch." \
  "merge" "submit_pr" <<'EOT'
Usage: gee submit_pr

Merges an approved pull request.
EOT
def gee__submit_pr(): gee__pr_submit "$@"; }
def gee__merge(): gee__pr_submit "$@"; }
def gee__pr_submit():
  _startup_checks

  _check_cwd
  _check_gh_auth

  # Check the status of the PR.
  local -a pr_vars=()
  mapfile -t pr_vars < \
    <("${GH}" pr view --json number,state,isDraft --jq '.number, .state, .isDraft')
  local pr_number="${pr_vars[0]}"
  local pr_state="${pr_vars[1]}"
  local pr_is_draft="${pr_vars[2]}"
  if [[ "${pr_state}" == "MERGED" ]]; then
    _fatal "Associated PR #${pr_number} has already been merged."
  fi
  if [[ "${pr_state}" != "OPEN" ]]; then
    _gh pr view
    _fatal "An open PR was not found."
  fi
  if [[ "${pr_is_draft}" == "true" ]]; then
    _fatal "Open PR #${pr_number} is still a draft."
  fi
  # Presubmit check: does remote branch exist?
  local CURRENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  if ! _remote_branch_exists origin "${CURRENT_BRANCH}"; then
    _fatal "Remote branch ${CURRENT_BRANCH} does not exist."
  fi
  # Presubmit check: Are there uncommitted local changes?
  local DIFFS
  DIFFS="$("${GIT}" diff --name-only | wc -l)"
  if (( "${DIFFS}" != 0 )); then
    _fatal "Will not submit: current branch has uncommitted changes."
  fi
  # Presubmit check: Is the remote branch in sync with the local branch:
  local -a COUNTS
  read -r -a COUNTS < \
    <("${GIT}" rev-list --left-right --count "${CURRENT_BRANCH}...origin/${CURRENT_BRANCH}")
  if [[ "${COUNTS[1]}" -gt 0 ]]; then
    _warn "Remote origin/${CURRENT_BRANCH} is ${COUNTS[1]} commits ahead of local ${CURRENT_BRANCH}."
    _fatal "Will not submit PR."
  fi
  if [[ "${COUNTS[0]}" -gt 0 ]]; then
    _warn "Local ${CURRENT_BRANCH} is ${COUNTS[0]} commits ahead of remote origin/${CURRENT_BRANCH}."
    _fatal "Will not submit PR."
  fi

  # Presubmit check: Are presubmit checks passing?
  if ! gee__pr_check; then
    if ! _confirm_default_no \
      "Presubmit checks did not succeed.  Proceed anyway? (y/N)  "; then
      _fatal "PR submission aborted."
    fi
  fi

  # Get some information about this PR before we merge.
  _set_main
  local MERGEBASE
  MERGEBASE="$( "${GIT}" merge-base "upstream/${MAIN}" "origin/${CURRENT_BRANCH}" )"
  _info "Merge base: ${MERGEBASE}"
  local -a COMMITS
  mapfile -t COMMITS < <( "${GIT}" log --pretty=oneline "${MERGEBASE}"..HEAD | awk '{print $1}' )
  local -a FILES
  mapfile -t FILES < <( "${GIT}" diff --name-only "${MERGEBASE}"..HEAD )
  _info "PR contains ${#COMMITS[@]} commits, changing ${#FILES[@]} files."

  # Extract the title and first comment as the commit message.
  local BODYFILE
  BODYFILE="$(mktemp -t pr.message.XXXXXX.txt)"
  TEMPFILE="$(mktemp -t pr.temp.XXXXXX.txt)"
  "${GH}" pr view > "${TEMPFILE}"
  grep "^title:" "${TEMPFILE}" | sed 's/title:\s*//' >"${BODYFILE}"
  echo "" >> "${BODYFILE}"
  sed -n '/^--/,$p' "${TEMPFILE}" | tail +2 >> "${BODYFILE}"
  rm "${TEMPFILE}"

  _info "Commit message will be:"
  cat "${BODYFILE}"

  local FROM_BRANCH="${GHUSER}:${CURRENT_BRANCH}"
  if [[ "${UPSTREAM}" == "${GHUSER}" ]]; then
    # gh-cli treats this as a special case for some reason.
    FROM_BRANCH="${CURRENT_BRANCH}"
  fi
  # Tag the old head commit so we can keep track after squashing:
  _git tag --force "${CURRENT_BRANCH}-unsquashed" "${CURRENT_BRANCH}"

  _gh pr merge \
    --squash \
    --body-file "${BODYFILE}" \
    --repo "${UPSTREAM}/${REPO}" \
    "${FROM_BRANCH}"
  rm "${BODYFILE}"
  _update_main

  # This check is of low value (gr pr merge is reliable), and occasionally
  # reports false negatives (ie. if two PRs changed the same file, but
  # gh pr merge was able to merge the changes without requiring the user
  # to update the source branch first).  This check is disabled for now:
  #
  # # Confirm that the merge was successful:
  # mapfile -t DIFFS < <( "${GIT}" diff --name-only \
  #     "upstream/${MAIN}..${CURRENT_BRANCH}" -- "${FILES[@]}" )
  # if (( "${#DIFFS[@]}" != 0 )); then
  #   # TODO(jonathan): should this be a warning?
  #   _fatal "Uh oh!  Even after merge, these files have local changes:" \
  #     "  ${DIFFS[*]}"
  # fi

  # Reset this branch to now contain the squash-merged commit:
  _checkout_or_die "${CURRENT_BRANCH}"  # make sure we're in the right branch.
  _git checkout -B "${CURRENT_BRANCH}" "upstream/${MAIN}"
  _push_to_origin "+${CURRENT_BRANCH}"

  # After a squash merge, we have a potential problem for child (and
  # grandchild) branches of this one.  When they attempt to rebase, they will
  # end up attempting to replay commits that were already squash-merged,
  # leading to conflicts.  To avoid these conflicts, we rebase all branches of
  # the current branch now.

  # 1. Check for child branches of the change we just squash-merged.

  # 1a. By using the "git branch --contains" command, we get all descendants
  # (grandchildren, etc.), even if the branch isn't listed as a child in gee's
  # branch ancestry metadata.  We look for branches that contain the first
  # commit in the sequence of commits that were squash-merged, since all
  # derived branches must at least contain that one commit.  (Note that COMMITS
  # are in reverse chronological order, so COMMITS[-1] is the earliest commit.)
  local FIRST_SQUASHED_COMMIT="${COMMITS[-1]}"
  local -a GITKIDS=()
  _read_cmd GITKIDS \
    "${GIT}" branch --format "%(refname:short)" --contains "${FIRST_SQUASHED_COMMIT}"

  # 1b. The above command can fail if a parent branch was rebased, and a child branch
  # wasn't, in such as way that "FIRST_SQUASHED_COMMIT" is no longer part of that branch.
  # This happens rarely but often enough that we should use gee's parents metadata as
  # a fallback.  Better to rebase too many branches than too few.
  local -a ALT_KIDS=()
  _read_cmd ALT_KIDS _gee_get_all_children_of "${CURRENT_BRANCH}"

  # 1c. Uniquify the list of kids, and also remove all branches that don't
  # exist anymore.  TODO(jonathan): prune dead branches from parents file.
  local -A UNIQUE_KIDS=()
  local K
  for K in "${GITKIDS[@]}" "${ALT_KIDS[@]}"; do
    if _local_branch_exists "${K}"; then
      UNIQUE_KIDS["${K}"]=1;
    fi
  done
  local -a KIDS=()
  KIDS+=( "${!UNIQUE_KIDS[@]}" )  # get keys of UNIQUE_KIDS

  # 1d. Bail early if there are no kids.
  if [[ "${#KIDS[@]}" -eq 0 ]]; then
    # Our job is done here.
    return 0
  fi

  # 2. Update all derived branches to be rebased onto the squash-merged commit.
  _warn "The following branches contain the commits that were just" \
        "squash-merged, and need to be rebased to avoid future" \
        "merge conflicts: ${KIDS[*]}"
  if _confirm_default_yes "Rebase child branches now? (Y/n)  "; then
    local CHILD
    for CHILD in "${KIDS[@]}"; do
      _banner "Rebasing ${CHILD}"
      # performs: git rebase --onto squashed_parent unsquashed_parent child
      _safer_rebase "${CHILD}" "${CURRENT_BRANCH}-unsquashed" "${CURRENT_BRANCH}"
    done
  fi
  # Because ${CURRENT_BRANCH} still exists and contains the squash-merged
  # tree, there is no need to change the parentage of the branch tree
  # (unlike gee__remove_branch, below).
}

##########################################################################
# pr_rollback command
##########################################################################

_register_help "pr_rollback" \
  "Create a rollback PR for a specified PR." <<'EOT'
Usage: gee pr_rollback <PR>

Creates a branch named `rollback_<PR>`, attempts to revert the commit
associated with that PR, and if successful, creates a new PR that rolls
back the specified PR.

Example:

    gee pr_rollback 1234
EOT
def gee__pr_rollback():
  _check_cwd
  _check_gh_auth

  local PR="$1"
  if [[ -z "${PR}" ]]; then
    _fatal "Must specify a PR number to roll back."
  fi

  _set_main
  _checkout_or_die "${MAIN}"
  local BR="rollback_${PR}"
  if _local_branch_exists "${BR}"; then
    _fatal "Branch ${BR} already exists, remove and try again?"
  fi
  gee__mkbr "${BR}"
  _checkout_or_die "${BR}"

  local -a LOGLINES=()
  local RE='\(#'"${PR}"'\)$'
  mapfile -t LOGLINES < \
    <( "${GIT}" log --pretty=format:"%h %s" | grep -E "${RE}" )
  if (( "${#LOGLINES[@]}" == 0 )); then
    _fatal "Could not find any commits labelled \"${RE}\""
  fi
  if (( "${#LOGLINES[@]}" > 1 )); then
    _fatal "When searching for \"${RE}\", found multiple possible commits:" \
      "${LOGLINES[@]}"
  fi

  local COMMIT DESC
  read -r COMMIT DESC <<< "${LOGLINES[0]}"
  _info "Identified commit \"${COMMIT}\": ${DESC}"

  _git revert --no-edit "${COMMIT}"  # creates a new commit
  _git push --quiet -u origin "${BR}"
  printf >&2 "\n"

  local BODYFILE
  BODYFILE="$(mktemp -t prbody.XXXXXX.txt)"
  (
    printf "gee-generated rollback of PR #%s (was commit %s).\n" "${PR}" "${COMMIT}"
    printf "\n"
    printf "Reason for rollback:\n"
    printf "\n"
    printf "Original commit message:\n"
    printf "========================\n"
    printf "\n"
    git log --format=%B -n 1 "${COMMIT}"
  ) >"${BODYFILE}"

  _choose_one \
    "Which editor do you want to use to edit the PR description? ([V]im, [e]macs, [a]bort)  " \
    "VvEeAa" "v"
  case "${RESP}" in
    [vV]*) vim "${BODYFILE}"; ;;
    [eE]*) emacs "${BODYFILE}"; ;;
    [aA]*) _fatal "Aborted."; ;;
  esac

  local TITLE
  TITLE="Rollback of PR #${PR}"

  local -a PR_ARGS
  PR_ARGS+=(
    --repo "${UPSTREAM}/${REPO}"
    --title "${TITLE}"
    -H "${GHUSER}:${BR}"
    --body-file "${BODYFILE}"
  )

  _gh pr create "${PR_ARGS[@]}"
  gee__codeowners --comment
  _gh_pr_view
  rm "${BODYFILE}"
}

##########################################################################
# remove_branch command
##########################################################################

_register_help "remove_branch" "Remove a branch." "rmbr" <<'EOT'
Usage: gee remove_branch <branch-name>

Removes a branch and it's associated directory.
EOT

def gee__rmbr(): gee__remove_branch "$@"; }
def gee__remove_branch():
  _startup_checks

  local BR="$1";
  if [[ -z "${BR}" ]]; then
    BR="$(_get_current_branch)"
    if [[ -z "${BR}" ]]; then
      _fatal "Must specify a branch name to remove."
    fi
  else
    shift
  fi

  _banner "Deleting ${BR}"
  _checkout_or_die "${BR}"
  _set_main
  local -a  counts=()
  read -r -a counts < <("${GIT}" rev-list --left-right --count "${MAIN}...${BR}")
  if (( counts[1] != 0 )); then
    _warn "Branch \"${BR}\" is ${counts[1]} commit(s) ahead of ${MAIN}."
    _confirm_or_exit "Are you sure you want to force-remove branch ${BR}? (y/N) "
    _info "As you wish."
  fi
  if [[ -n "$("${GIT}" status --porcelain)" ]]; then
    _warn "Branch \"${BR}\" contains uncommitted changes."
    _confirm_or_exit "Are you sure you want to force-remove branch ${BR}? (y/N) "
    _info "Whatever you say, boss."
  fi

  local SHA
  SHA="$("${GIT}" reflog | head -n 1 | awk '{print $1}' )"

  _checkout_or_die "${MAIN}"
  # --force is required for git v2.17.1, or the remove operation will always fail.
  _git worktree remove --force "${BR}"
  _git branch -D "${BR}"
  if _remote_branch_exists origin "${BR}"; then
    _git_can_fail push --quiet origin --delete "${BR}" | cat
  else
    _info "Not deleting remote branch ${BR}: was never created."
  fi

  # Remove branch from parents file, and fix up children's parents.
  _read_parents_file
  local PREV_PARENT
  PREV_PARENT="${PARENTS["${BR}"]}"
  if [[ -z "${PREV_PARENT}" ]]; then PREV_PARENT="${MAIN}"; fi
  unset PARENTS["${BR}"]
  local K
  for K in "${!PARENTS[@]}"; do
    if [[ "${PARENTS["$K"]}" == "${BR}" ]]; then
      PARENTS["$K"]="${PREV_PARENT}"
    fi
  done
  _write_parents_file

  _info "Deleted ${BR}.  To undo: gee make_branch ${BR} ${SHA}"
}

##########################################################################
# fix command
##########################################################################

_register_help "fix" "Run automatic code formatters over changed files only." <<'EOT'
Usage: gee fix [<files>]

Looks for a "fix_format.sh" script in the root directory of the current branch,
and runs it.  This script runs a set of language formatting tools over either:

  - the files specified on the command line, or
  - if no files are specified, all of the locally changed files in this
    branch.

Note: "gee fix" (which fixes code in a branch) is different from "gee repair"
(which checks the gee directory for errors and repairs them).

Note: "fix_format.sh" used to be integrated into gee, but has been separated
out as formatting rules are highly project specific.
EOT

def gee__fix():
  _startup_checks

  local BDIR
  BDIR="$(_get_branch_rootdir)"
  if [[ -x "${BDIR}/fix_format.sh" ]]; then
    _cmd "${BDIR}/fix_format.sh" "$@"
  else
    _fatal "No fix_format.sh script exists in this repository."
  fi
}

##########################################################################
# gcd command
##########################################################################
# TODO(jonathan): add an option to make a branch if missing.
# TODO(jonathan): make this work from outside a gee branch.
# TODO(jonathan): add g4d-like functionality such as gcd branch/some/path
#                 and gcd branch@? to find last-edited directory.
# TODO(jonathan): Maybe "change_branch" (chb? cbr? chbr?) is a better name.

_register_help "gcd" "Change directory to another branch." <<'EOT'
Usage: gcd [-b] <branch>[/<path>]

The "gee gcd" command is not meant to be used directly, but is instead designed
to be called from the "gcd" bash function, which can be imported into your
environment with gee's "bash_setup" command, like this:

    eval "$(gee bash_setup)"

(This command should be added to your .bashrc file.)

Once imported, the "gcd" function can be used to change directory between
branches.

If only "<branch>" is specified, "gcd" will change directory to the same
relative directory in another branch.  If "<branch>/<path>" is specified,
"gcd" will change directory to the specified path beneath the specified
branch.

For example:

    cd ~/gee/enkit/branch1/foo/bar
    # now in ~/gee/enkit/branch1/foo/bar
    gcd branch2
    # now in ~/gee/enkit/branch2/foo/bar
    gcd branch3/foo
    # now in ~/gee/enkit/branch3/foo

gcd also updates the following environment variables:

* BROOT always contains the path to the root directory of the current branch.
* BRBIN always contains the path to the bazel-bin directory beneath root.

EOT

def gee__gcd():
  local TARGET BRANCH REL_PATH OPT_B
  OPT_B=0
  if [[ "$1" == "-b" ]]; then
    OPT_B=1
    shift
  fi
  TARGET="$1"
  _set_main
  if [[ -z "${TARGET}" ]]; then
    TARGET="${MAIN}"
  fi

  if ! _in_gee_branch; then
    cd "${GEE_DIR}/${REPO}/${MAIN}"
  fi
  if ! _in_gee_branch; then
    _die "Can't find ${REPO}/${MAIN} branch.  Something is very wrong here."
  fi

  # Parse the TARGET specifier.
  if [[ "${TARGET}" == *"/"* ]]; then
    # TARGET is <branch>/<directory>:
    BRANCH="${TARGET%%/*}"
    REL_PATH="${TARGET#*/}"
  else
    # otherwise, target is just the branch name:
    BRANCH="${TARGET}"
    REL_PATH="$(git rev-parse --show-prefix)"
  fi

  # check whether BRANCH exists:
  if ! _local_branch_exists "${BRANCH}"; then
    if (( OPT_B == 1 )); then
      gee__mkbr "${BRANCH}" >&2
      if ! _local_branch_exists "${BRANCH}"; then
        _fatal "Branch \"${REPO}/${BRANCH}\" could not be created."
      fi
    else
      _fatal "Branch \"${REPO}/${BRANCH}\" does not exist.  Make use make_branch?"
    fi
  fi

  local DIR
  _update_branch_to_worktree
  DIR="${BRANCH_TO_WORKTREE["${BRANCH}"]}"
  if [[ -z "${DIR}" ]]; then
    _die "Branch \"${REPO}/${BRANCH}\" exists but isn't in worktree?"
  fi
  if [[ ! -d "${DIR}" ]]; then
    _die "Branch \"${REPO}/${BRANCH}\" exists but ${DIR} is missing."
  fi

  # Trim DIR to the longest path that exists in this branch.
  local P PREV_IFS
  PREV_IFS="${IFS}"
  IFS="/"; for P in ${REL_PATH}; do
    if [[ -d "${DIR}/${P}" ]]; then
      DIR="${DIR}/${P}"
    else
      break
    fi
  done
  IFS="${PREV_IFS}"
  echo "${DIR}"
}

##########################################################################
# hello command
##########################################################################

_register_help "hello" "Check connectivity to github." <<'EOT'
Usage: gee hello

Verifies that the user can communicate with github using ssh.

For more information:
  https://docs.github.com/en/github/authenticating-to-github/connecting-to-github-with-ssh
EOT

def gee__hello():
  _get_ghuser_via_ssh
  if [[ -z "${_QUIET}" ]]; then
    _info "Hello, ${GHUSER}.  Connectivity to github is AOK."
  fi
}

##########################################################################
# create_ssh_key command
##########################################################################

_register_help "create_ssh_key" "Create and enroll an ssh key." <<'EOT'
Usage: gee create_ssh_key

This command will attempt to re-enroll you for ssh access to github.

Normally, "gee init" will ensure that you have ssh access.  This command
is only available if something else has gone wrong requiring that keys
be updated.
EOT

def gee__create_ssh_key():
  _ssh_enroll
  gee__hello
}

##########################################################################
# share command
##########################################################################

_register_help "share" "Share your branch." <<'EOT'
Usage: gee share

Displays URLs that you can paste into emails to share the contents of
your branch with other users (in advance of sending out a PR).
EOT

def gee__share():
  _startup_checks

  local CURRENT_BRANCH PARENT_BRANCH
  CURRENT_BRANCH="$(_get_current_branch)"
  PARENT_BRANCH="$(_get_parent_branch)"
  _info "These URLs are useful for sharing:"
  echo  "  https://github.com/${GHUSER}/${REPO}/compare/${PARENT_BRANCH}...${CURRENT_BRANCH}"
  echo  "  https://github.com/${GHUSER}/${REPO}/tree/${CURRENT_BRANCH}"
}

##########################################################################
# repair command
##########################################################################

_register_help "repair" "Repair your gee workspace." <<'EOT'
Usage: gee repair <command>

Gee tries to control some metadata and attempts to file away some of the
sharp edges from git.  Sometimes, bypassing gee to use git directly can
cause some of gee's metadata to become stale.  This command fixes up
any missing or incorrect metadata.
EOT

def _gee_fix_remote_origin_fetch_config():
  # An old version of gee incorrectly configured the remote.origin.fetch
  # parameter.  This command checks for an incorrect value and corrects
  # it.
  local REMOTE_ORIGIN_FETCH CORRECT
  CORRECT="+refs/heads/*:refs/remotes/origin/*"
  REMOTE_ORIGIN_FETCH="$("${GIT}" config --get-all remote.origin.fetch)"
  if [[ "${REMOTE_ORIGIN_FETCH}" != "${CORRECT}" ]]; then
    _warn "remote.origin.fetch was incorrectly set to:" "${REMOTE_ORIGIN_FETCH}"
    _git config --replace-all remote.origin.fetch "${CORRECT}"
    _info "Fixed remote.origin.fetch configuration."
  fi
}

def gee__repair():
  # Make sure all tools are available
  _install_tools

  _gee_fix_remote_origin_fetch_config

  if ! _gh auth status; then
    _warn "Looks like you were not able to authenticate to the github REST API."
    _info "Let's try re-authenticating to github:"
    _gh auth login

    if ! _gh auth status; then
      _warn "Still can't authenticate to github!  You might need more help."
      # Gallantly attempt to carry on in the face of adversity...
    fi
  fi

  if ! _in_gee_repo; then
    _fatal "Not in a gee repo directory, aborting further repairs."
  fi

  _info "Checking each directory in ${REPO_DIR}..."
  local DIR BRANCH OBRANCH
  for DIR in "${REPO_DIR}"/*; do
    BRANCH="$(basename "${DIR}")"
    cd "${DIR}"
    if [[ ! -e ./.git ]]; then
      _warn "Skipping non-git directory ${DIR}"
      continue
    fi
    OBRANCH="$(_get_current_branch)"

    # Give user opportunity to abort in-progress rebase operations:
    if _is_rebase_in_progress; then
      _warn "Rebase in progress on branch ${OBRANCH}"
      mapfile -t STATUS < <( "${GIT}" status );
      _info "${STATUS[@]}"
      if _confirm_default_no "Do you want to abort this rebase operation now?"; then
        _git rebase --abort
        if _is_rebase_in_progress; then
          _die "Error while aborting rebase operation."
        fi
      fi
    fi

    # Make sure the worktree directory points to the right branch:
    if [[ "${OBRANCH}" != "${BRANCH}" ]]; then
      _warn "${BRDIR} pointed to branch ${OBRANCH} instead of ${BRANCH}."
      _git checkout "${BRANCH}"
      _info "... Fixed."
    fi
  done

  _info "Checking each branch in the local repository..."
  local -a ALL_BRANCHES=()
  mapfile -t ALL_BRANCHES < <( "${GIT}" branch --format="%(refname:short)" )
  for BRANCH  in "${ALL_BRANCHES[@]}"; do
    # make sure each branch has a worktree directory:
    _checkout_or_die "${BRANCH}"
  done

  # Repair the PARENT file if it gets corrupted or removed, by attempting to
  # guess the parent for each branch.
  _info "Checking the parents file..."
  _read_parents_file
  local DIRTY=0
  _set_main
  for BRANCH  in "${ALL_BRANCHES[@]}"; do
    if [[ "${BRANCH}" == "${MAIN}" ]]; then continue; fi
    if [[ -z "${PARENTS["${BRANCH}"]}" ]]; then
      _warn "${BRANCH} is missing \"parent\" metadata."
      _checkout_or_die "${BRANCH}"
      # Try to guess which branch is this branch's parent:
      # TODO(jonathan): There is almost certainly a better way using rev-list.
      local PARENT
      PARENT="$(git show-branch | sed "s/].*//" | grep "\*" \
        | grep -v -w "${BRANCH}" | head -n1 \
        | perl -pe 'm/\[([a-zA-Z0-9_-]+?)(\^\d+)?(~\d+)?$/; $_ = $1;' )"
      if [[ -z "${PARENT}" ]]; then
        PARENT="${MAIN}"
      fi
      PARENTS["${BRANCH}"]="${PARENT}"
      DIRTY=1
      _info "Guessed that ${PARENT} is the parent of ${BRANCH}"
    fi
  done
  if (( DIRTY )); then
    _write_parents_file
  fi

  _info "Done."
}

##########################################################################
# restore_all_branches command
##########################################################################

_register_help "restore_all_branches" "Check out all remote branches." <<'EOT'
Usage: gee restore_all_branches

Gee looks up all branches on the origin remote, and makes sure an equivalent
branch is checked out and updated locally.

Note that gee isn't able to restore parentage metadata in this way.  Be
sure to invoke `gee set_parent` in branches that benefit from this.
EOT

def gee__restore_all_branches():
  # Make sure all tools are available
  _install_tools

  _gee_fix_remote_origin_fetch_config

  local OLDEST_COMMIT
  OLDEST_COMMIT="$(date --date="-${CLONE_DEPTH_MONTHS} months" +"%Y-%m-%d")"
  _info "Fetching all commits in origin since ${OLDEST_COMMIT}"
  _git fetch origin --shallow-since "${OLDEST_COMMIT}"

  local -a BRANCHES=()
  mapfile -t BRANCHES < <( \
    git branch -r --format="%(refname:short)" | grep ^origin/ | grep -v HEAD )

  _set_main
  local RB
  for RB in "${BRANCHES[@]}"; do
    local B BDIR
    B="$(basename "${RB}")"
    BDIR="${REPO_DIR}/${B}"
    if [[ -d "${BDIR}" ]]; then
      _info "Branch ${B} already exists, skipping."
    else
      _info "Restoring ${B} from ${RB}"
      _checkout_or_die "${B}"
      local -a COUNTS
      read -r -a COUNTS < <("${GIT}" rev-list --left-right --count "${B}...${RB}" | cat)
      if (( COUNTS[1] != 0 )); then
        _git reset --hard "${RB}"
      else
        _info "${B} is already up to date."
      fi
    fi
  done

  _info "Running \"gee repair\" to reconstruct parents information."
  gee__repair

  _info "Done."
}

##########################################################################
# bash_setup command
##########################################################################

_register_help "bash_setup" "Configure the bash environment for gee." \
<<'EOT'
Usage: eval "$(~/bin/gee bash_setup)"

The "bash_setup" command emits a set of bash aliases and functions that
streamline the use of gee.  The following functions are exported:

  "gee": invokes "gee $@"
  "gcd": rapidly change between gee branch directories.

Additionally, the following functions can be used to customize your
command prompt with useful information about your git work tree:
  "gee_prompt_git_info": prints git-related information suitable for
    integrating into your own prompt.
  "gee_prompt_print": Prints a string suitable for using as a prompt.
  "gee_prompt_set_ps1": Sets PS1 to the output of gee_prompt_print.

This custom git-aware prompt will keep you apprised of which git branch you are
in, and will also tell you important information about the status of your
branch (whether or not you are in the middle of a merge or rebase operation,
whether there are uncommitted changes, and more).

The easiest way to make use of the git-aware prompt is to modify your .bashrc
file to set PROMPT_COMMAND to "gee_prompt_set_ps1", as shown below:

    export PROMPT_COMMAND="gee_prompt_set_ps1"

This prompt can be customized by setting the following environment variables:

*  GEE_PROMPT: The PS1-style prompt string to put at the end of every prompt.
    Default:  `$' [\\!] \\w\033[0K\033[0m\n$ '`
   More info: https://www.man7.org/linux/man-pages/man1/bash.1.html#PROMPTING
*  GEE_PROMPT_BG_COLOR: The ANSI color to use as the background (default: 5).
*  GEE_PROMPT_FG1_COLOR: The foreground color for git-related info (default: 9).
*  GEE_PROMPT_FG2_COLOR: The foreground color for GEE_PROMPT (default: 3).

Easter egg: Use the "gee_prompt_test_colors" command to view a test pattern
of the basic 4-bit ANSI color set.

If you need further customization, you are encouraged to write your own
version of gee_prompt_set_ps1.

Also sets GEE_BINARY to point to this copy of gee.
EOT

def gee__bash_setup():
  cat <<'END_OF_BASH_SETUP'
# bash functions for gee
#
# This output is meant to be loaded into your shell with this command:
#
#   eval "$(~/bin/gee bash_setup)"

def gee():
  # TODO(jonathan): now that gee isn't being invoked by enkit, perhaps
  # this function isn't needed anymore?  But if I remove it, users with
  # incorrect PATHs will break.
  if [[ -n "${GEE_BINARY}" ]]; then
    "${GEE_BINARY}" "$@";
  elif [[ -x ~/bin/gee ]]; then
    # use locally installed gee if available.
    ~/bin/gee "$@";
  else
    # search the PATH for gee:
    local path
    path="$(which gee)"
    if [[ -z "${path}" ]]; then
      echo "I'm sorry, but I can't find gee anywhere!"
      return
    fi
    "${path}" "$@";
  fi
}

def gcd():
  if (( "$#" == 0 )); then
    gee help gcd
    return 1
  fi

  local D="$(gee gcd "$@")"
  if [[ -n "${D}" ]]; then
    cd "${D}"
  fi
  export BROOT="$(git rev-parse --show-toplevel)"
  export BRBIN="${BROOT}/bazel-bin"
}

def _gee_completion_branches():
  shift  # discard
  local REPO=internal
  local DIR="$(realpath --relative-base="${HOME}/gee" "${PWD}")"
  if [[ -n "${DIR}" ]] && [[ "${DIR}" != "." ]] && [[ "${DIR}" != /* ]]; then
    REPO="$(cut -d/ -f1 <<< "${DIR}")"
  fi
  COMPREPLY=($(cd "${HOME}/gee/${REPO}"; compgen -f -X \\.* "$@"))
}

END_OF_BASH_SETUP
  # Note that we do the escaping differently for this second part:
  cat <<END_OF_BASH_COMPLETION_SETUP
# Command completions:
def _gee_completion():
  local cur prev
  cur="\${COMP_WORDS[COMP_CWORD]}"
  case "\${COMP_CWORD}" in
    1)
      COMPREPLY=(\$(compgen -W "${!LONGHELP[*]}" -- "\${cur}"))
      ;;
    2)
      prev="\${COMP_WORDS[COMP_CWORD-1]}"
      case "\${prev}" in
        diff|unpack|revert)
          COMPREPLY=(\$(compgen -f -- "\${cur}"))
          ;;
        set_parent|gcd|rmbr|remove_branch)
          _gee_completion_branches gcd "\${cur}"
          ;;
        help)
          COMPREPLY=(\$(compgen -W "${!LONGHELP[*]}" -- "\${cur}"))
          ;;
      esac
  esac
}

complete -F _gee_completion gee
complete -F _gee_completion_branches gcd
END_OF_BASH_COMPLETION_SETUP
  cat <<'END_OF_GEE_PROMPT'
# This git-aware prompt implementation owes a debt to Shawn O. Pearce's
# "git-prompt.sh" implementation, found here:
# https://github.com/git/git/blob/master/contrib/completion/git-prompt.sh

def gee_prompt_update_git_info():
  GEE_PROMPT_BRANCH=""
  local git_dir="" is_inside_work_tree="" toplevel
  read -r -d '' git_dir is_inside_work_tree toplevel < \
    <( git rev-parse --git-dir \
                     --is-inside-work-tree \
                     --show-toplevel \
                     2>/dev/null)
  if [[ "true" != "${is_inside_work_tree}" ]]; then return 0; fi

  local repo=""
  if [[ "${toplevel}" =~ ^"${HOME}"/gee/(.+)/ ]]; then
    repo="$(printf "%.3s:" "${BASH_REMATCH[1]}:")"
  fi

  local branch
  read -r branch < <( git rev-parse --abbrev-ref HEAD )
  GEE_PROMPT_BRANCH="${branch}"

  local mode=""
  local step=""
  local total=""
  if [[ -d "${git_dir}/rebase-merge" ]]; then
    mode="REBASING:MERGE"
    read -r step < "${git_dir}/rebase-merge/msgnum"
    read -r total < "${git_dir}/rebase-merge/end"
    read -r branch < "${git_dir}/rebase-merge/head-name"
    branch="$(basename "${branch}")"
  elif [[ -d "${git_dir}/rebase-apply" ]]; then
    mode="REBASING:APPLY"
    read -r step < "${git_dir}/rebase-apply/next"
    read -r total < "${git_dir}/rebase-apply/last"
    read -r branch < "${git_dir}/rebase-apply/head-name"
    branch="$(basename "${branch}")"
  elif [[ -f "${git_dir}/MERGE_HEAD" ]]; then
    mode="MERGING"
  elif [[ -f "${git_dir}/CHERRY_PICK_HEAD" ]]; then
    mode="CHERRY_PICKING"
  elif [[ -f "${git_dir}/REVERT_HEAD" ]]; then
    mode="REVERTING"
  elif [[ -f "${git_dir}/BISECT_LOG" ]]; then
    mode="BISECTING"
  elif [[ -f "${git_dir}/sequencer/todo" ]]; then
    local todo
    read -r todo < "${git_dir}/sequencer/todo"
    if [[ "${todo}" =~ ^(p|pick)\  ]]; then
      mode="CHERRY_PICKING"
    elif [[ "${todo}" =~ ^revert\  ]]; then
      mode="REVERTING"
    fi
  fi

  # try some other ways to determine the branch name:
  if [[ -z "${branch}" ]]; then
    branch="$(git name-rev --name-only HEAD)"
  fi

  local complete_mode=""
  if [[ -n "${mode}" ]]; then
    complete_mode=":${mode}"
  fi
  if [[ -n "${step}" ]] && [[ -n "${total}" ]]; then
    complete_mode+=" (step ${step}/${total})"
  fi

  local changes=""
  git diff --no-ext-diff --quiet || changes="*"
  local cached=""
  git diff --no-ext-diff --cached --quiet || cached="+"
  local stash_depth
  read -r stash_depth < <(git stash list | wc -l)
  if [[ "${stash_depth}" -eq 0 ]]; then
    stash_depth=""
  else
    stash_depth=" S=${stash_depth}"
  fi

  GEE_PROMPT_INFO="$(printf "(%s%s%s%s%s%s)" \
    "${repo}" "${branch}" "${complete_mode}" \
    "${changes}" "${cached}" "${stash_depth}")"
}

def gee_prompt_git_info():
  gee_prompt_update_git_info
  echo "${GEE_PROMPT_INFO}"
}

def __gee_prompt_set_colors():
  local fg="$1"
  local bg="$2"
  local bold="$3"
  local code=$'\033[0'
  if [[ "${bold}" == "1" ]]; then
    code+=";1"
  fi
  if (( "${fg}" > 7 )); then
    code+=";$(( fg - 8 + 90 ))"
  else
    code+=";$(( fg + 30 ))"
  fi
  if (( "${bg}" > 7 )); then
    code+=";$(( bg - 8 + 100 ))"
  else
    code+=";$(( bg + 40 ))"
  fi
  code+="m"
  printf "%s" "${code}"
}

def __gee_prompt_set_window_title():
  local title="$1"
  local ESC=$'\033'
  local SLASH=$'\134'
  printf "${ESC}k${ESC}${SLASH}${ESC}k%s${ESC}${SLASH}" "$1"
}

def gee_prompt_test_colors():
  local fg bg bold
  for bg in $(seq 0 15); do
    for bold in 0 1; do
      printf "%02d: " "${bg}"
      for fg in $(seq 0 15); do
        __gee_prompt_set_colors "${fg}" "${bg}" "${bold}"
        printf " %02d " "${fg}"
      done
      printf $'\033[0m %s\n' "${bold}"
    done
  done
}

def gee_prompt_print():
  gee_prompt_update_git_info
  local p="${GEE_PROMPT}"
  if [[ -z "${p}" ]]; then
    p=" [\\!] \\w\033[0K\033[0m\n$ "
  fi

  local bg="${GEE_PROMPT_BG_COLOR:-1}"
  local fg1="${GEE_PROMPT_FG1_COLOR:-15}"
  local bold1="${GEE_PROMPT_FG1_BOLD:-1}"
  local fg2="${GEE_PROMPT_FG2_COLOR:-0}"
  local bold2="${GEE_PROMPT_FG2_BOLD:-0}"

  printf "%s" \
    "$(__gee_prompt_set_window_title "${GEE_PROMPT_BRANCH:-bash}")" \
    "$(__gee_prompt_set_colors "${fg1}" "${bg}" "${bold1}" )" \
    "${GEE_PROMPT_INFO}" \
    "$(__gee_prompt_set_colors "${fg2}" "${bg}" "${bold2}" )" \
    "${p}" \
    $'\033[0m'
}

def gee_prompt_set_ps1():
  export PS1
  PS1="$(gee_prompt_print)"
}

# To enable this prompt, the user must opt-in by setting:
# export PROMPT_COMMAND="gee_prompt_set_ps1"

END_OF_GEE_PROMPT
  echo "export GEE_BINARY=\"$(readlink -f "$0")\""  # make this gee the default gee.
}

##########################################################################
# upgrade command
##########################################################################

_register_help "upgrade" "Upgrade the gee tool." <<'EOT'
Usage: gee upgrade [--check]
EOT

def gee__upgrade():
  local GEE_PATH
  GEE_PATH="$(readlink -f "$0")"
  if _check_gee_version_is_current; then
    _info "${GEE_PATH} is already up-to-date."
  else
    if _confirm_default_yes "A new version of gee is available.  Install? (Y/n)  "; then
      # make a backup
      mv -f "${GEE_PATH}"{,.backup}
      cd "$(dirname "${GEE_PATH}")"
      _cmd enkit astore download "${GEE_ON_ASTORE}"
      if ! [[ -f "${GEE_PATH}" ]]; then
        _warn "Could not create \"${GEE_PATH}\", restoring backup."
        _cmd mv -f "${GEE_PATH}"{.backup,}
        if ! [[ -f "${GEE_PATH}" ]]; then
          _die "Could not even restore backup."
        fi
      fi
      chmod +x "${GEE_PATH}"
      if [[ -f "${GEE_PATH}".backup ]]; then
        rm -f "${GEE_PATH}".backup
      fi
      _info "${GEE_PATH} has been upgraded."
    fi
  fi
}

##########################################################################
# version command
##########################################################################

_register_help "version" "Print tool version information." <<'EOT'
Usage: gee version
EOT

def gee__version():
  echo "$0: version ${VERSION}"
  md5sum "$0"
  local TOOL_ERROR=0
  for tool in "${GIT}" "${GH}" "${JQ}"; do
    if ! [[ -x "${tool}" ]]; then
      _warn "${tool}: not installed!"
      TOOL_ERROR=1
    else
      "${tool}" --version
    fi
  done
  if (( TOOL_ERROR )); then
    _info "Run \"gee repair\" to fix."
  fi
  if ! _check_gee_version_is_current; then
    _warn "A newer version of gee is available.  Run \"gee upgrade\" to install."
  fi
}

##########################################################################
# help command
##########################################################################

_register_help "help" "Print more help about a command." <<'EOT'
Usage: gee help [<command>|usage|commands|markdown]

The "usage" option produces gee's manual.

The "commands" option shows a summary of all available commands.

The "markdown" option produce's gee's manual, in markdown format.
EOT

def _render_markdown_manual():
  local -a U=()
  mapfile -t U <<< "${USAGE//\{\{VERSION\}\}/"${VERSION}"}"
  local LINE
  local CODEBLOCK=1
  echo '```'
  for LINE in "${U[@]}"; do
    if [[ "${CODEBLOCK}" == 1 ]]; then
      if [[ "${LINE}" == "" ]]; then
        echo '```'
        CODEBLOCK=0
      fi
    else
      if [[ "${LINE}" == "    "* ]]; then
        echo '```shell'
        CODEBLOCK=1
      fi
    fi
    echo "${LINE}"
  done
  if [[ "${CODEBLOCK}" == 1 ]]; then
    echo '```'
  fi
  echo ""
  echo "## Command Summary"
  echo ""
  echo "| Command | Summary |"
  echo "| ------- | ------- |"
  local h h_cmd h_desc
  local BT='`'
  for h in "${HELP[@]}"; do
    IFS=": " read -r h_cmd h_desc <<< "${h}"
    echo "| <a href=\"#${h_cmd}\">${BT}${h_cmd}${BT}</a> | ${h_desc} |"
  done | sort
  echo ""
  echo "## Commands"
  echo ""
  for h in "${HELP[@]}"; do
    read -r -d ":" h_cmd h_desc <<< "${h}"
    echo "### ${h_cmd}"
    echo ""
    echo "${LONGHELP["${h_cmd}"]}" \
      | perl -p -e 's/^Usage: (.*?)$/Usage: `$1`/gmso'
    echo ""
  done
}

def gee__help():
  if [[ "$1" == "html" ]]; then
    _render_html_manual
  elif [[ "$1" == "markdown" ]]; then
    _render_markdown_manual
  else
    (
      if (( "$#" == 0 )); then
        set -- "usage"
      fi
      if [[ "$1" == "usage" ]]; then
        echo "${USAGE//\{\{VERSION\}\}/"${VERSION}"}"
        echo ""
      fi
      if [[ ("$1" == "usage") || ("$1" == "commands") ]]; then
        echo "## Commands:"
        echo ""
        for h in "${HELP[@]}"; do
          echo "  $h"
        done | sort | column -t -s ":"
        shift;
      fi
      while (( "$#" )); do
        local COMMAND="$1"
        shift
        if [ "${LONGHELP[${COMMAND}]+_}" ]; then
          echo "${LONGHELP[${COMMAND}]}"
        else
          echo "${COMMAND}: there is no help for this."
        fi
      done
    ) | "${PAGER}"
  fi
}

def gee__banner():
  _banner "$@"
}

##########################################################################
# main
##########################################################################

def main():
  if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    shift
    set -- "help" "$@"
  fi
  if (( "$#" == 0 )); then
    gee__help usage
    exit 0
  fi
  local cmdname="$1"; shift
  # Let's make pr-submit and pr_submit equivalent:
  cmdname="$(tr '-' '_' <<< "${cmdname}")"
  if type "gee__${cmdname}" >/dev/null 2>&1; then
    "gee__${cmdname}" "$@"
    ABNORMAL=0
  else
    echo "Unknown command ${cmdname}"
    echo ""
    gee__help commands
    ABNORMAL=0
    exit 2
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
