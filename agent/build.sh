#!/usr/bin/env bash
# Builds the client scanner agent into agent/dist/ (served by /api/agent/download).
# Windows and Linux cross-compile anywhere; the macOS build needs a Mac (cgo + Cocoa).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p dist

build() { # goos goarch output [extra ldflags]
  echo "building $3"
  CGO_ENABLED=0 GOOS="$1" GOARCH="$2" go build -trimpath -ldflags "-s -w ${4:-}" -o "dist/$3" .
}

build windows amd64 origami-agent-windows-amd64.exe "-H=windowsgui"
build linux amd64 origami-agent-linux-amd64
build linux arm64 origami-agent-linux-arm64

if [[ "$(uname -s)" == "Darwin" ]]; then
  for arch in arm64 amd64; do
    app="dist/mac-$arch/Origami Agent.app"
    rm -rf "dist/mac-$arch"
    mkdir -p "$app/Contents/MacOS"
    cp macos/Info.plist "$app/Contents/Info.plist"
    echo "building origami-agent-darwin-$arch.zip"
    CGO_ENABLED=1 GOOS=darwin GOARCH="$arch" go build -trimpath -ldflags "-s -w" -o "$app/Contents/MacOS/origami-agent" .
    (cd "dist/mac-$arch" && rm -f "../origami-agent-darwin-$arch.zip" && zip -qr "../origami-agent-darwin-$arch.zip" "Origami Agent.app")
    rm -rf "dist/mac-$arch"
  done
else
  echo "skipping macOS: build on a Mac to produce origami-agent-darwin-*.zip"
fi
