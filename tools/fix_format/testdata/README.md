<!--ts-->

- [Welcome](#welcome)
- [Legal things](#legal-things)
  - [Copyright and Intellectual Property](#copyright-and-intellectual-property)
  - [Third Party code](#third-party-code)
  - [Sharing](#sharing)
- [Writing code](#writing-code)
- [Using and building the code](#using-and-building-the-code)
  - [Building a project with bazel.](#building-a-project-with-bazel)
  - [Navigating the build tree](#navigating-the-build-tree)
- [Useful bazel commands](#useful-bazel-commands)
- [Using a docker image](#using-a-docker-image)
- [Supported languages and environments](#supported-languages-and-environments)
- [Git/github tips](#gitgithub-tips)

<!-- Added by: ccontavalli, at: Thu 18 Jun 2020 11:59:10 AM PDT -->

<!--te-->

# Welcome

Welcome to the main Enfabrica repository!

Unless you have a strong reason to put your code somewhere else, this is where
your code should live.

Right now, there are only a few directories:

- **fpga** - used for, well, fpga development. Peek in there to find out more.

- **poc** - used for Proof of Concepts. Quick hacks, utils, tests, or things
  that are useful to verify that your ideas are working, but are not otherwise
  expected to go into production without major efforts.

- **bazel** - used for rules and tools related to the bazel build system.

- **guidelines** - used for examples and guidelines related to this
  repository.

To learn to use this repository, keep reading!

`grip . 0.0.0.0`, it will start a web server with a preview.
