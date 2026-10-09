//go:build darwin

package main

import (
	"fmt"
	"os/exec"
)

// On macOS LaunchServices registers the scheme from Info.plist when the app is opened once.
func register() (string, error) {
	return "Origami Agent is installed. Go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	fmt.Println(msg)
	script := fmt.Sprintf("display notification %q with title %q", msg, "Origami Agent")
	_ = exec.Command("osascript", "-e", script).Run()
}
