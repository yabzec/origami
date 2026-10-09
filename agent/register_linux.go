//go:build linux

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func register() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return "", err
	}
	dataHome := os.Getenv("XDG_DATA_HOME")
	if dataHome == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dataHome = filepath.Join(home, ".local", "share")
	}
	appDir := filepath.Join(dataHome, "applications")
	if err := os.MkdirAll(appDir, 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(appDir, "origami-agent.desktop"), []byte(desktopEntry(exe)), 0o644); err != nil {
		return "", err
	}
	if out, err := exec.Command("xdg-mime", "default", "origami-agent.desktop", "x-scheme-handler/origami-agent").CombinedOutput(); err != nil {
		return "", fmt.Errorf("xdg-mime: %v: %s", err, out)
	}
	_ = exec.Command("update-desktop-database", appDir).Run() // optional on most desktops
	return "Origami Agent is installed. Keep this file where it is (" + exe + "), then go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	fmt.Println(msg)
	_ = exec.Command("notify-send", "Origami Agent", msg).Run()
}
