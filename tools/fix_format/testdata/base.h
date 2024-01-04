#ifndef EXAMPLE_LIB_BASE_H
#define EXAMPLE_LIB_BASE_H

// #include for generated protobuf code
// Dirname is the path to the proto file in the source repository
// (`example.proto` resides in `guidelines/proto` in this case) + the proto name
// with a `.pb.h` file extension.
#include <string>

#include "guidelines/proto/example.pb.h"
#include "sdk/lib/error/status_or.hh"

extern std::string Message();

extern error::StatusOr<std::unique_ptr<guidelines::proto::Config>> LoadConfig(const char *path);

#endif
