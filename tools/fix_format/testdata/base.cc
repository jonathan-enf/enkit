#include "base.h"

#include <fcntl.h>
#include <google/protobuf/io/zero_copy_stream_impl.h>
#include <google/protobuf/text_format.h>

#include <string>

#include "sdk/lib/error/assign_or_return.hh"
#include "sdk/lib/posix/open.hh"

using std::string;
using std::string_view;
using std::unique_ptr;

std::string Message() { return "Hello from a library, underlings"; }

error::StatusOr<unique_ptr<guidelines::proto::Config>> LoadConfig(const char *path) {
  ASSIGN_OR_RETURN(auto fd, posix::Open(path, O_RDONLY));
  // Need to unwrap fd twice - once for UniqueValue, once for OpaqueValue
  auto input_stream = google::protobuf::io::FileInputStream(GetValue(GetValue(fd)));

  auto config = std::make_unique<guidelines::proto::Config>();
  if (!google::protobuf::TextFormat::Parse(&input_stream, config.get())) {
    return error::Status(std::errc::bad_message, "failed to parse config");
  }
  return config;
}