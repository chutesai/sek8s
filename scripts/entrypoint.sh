#!/usr/bin/env bash
set -ex
admission-controller &
exec $@