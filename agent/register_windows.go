//go:build windows

package main

import (
	"os"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func register() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	for _, e := range windowsEntries(exe) {
		k, _, err := registry.CreateKey(registry.CURRENT_USER, e.Key, registry.SET_VALUE)
		if err != nil {
			return "", err
		}
		err = k.SetStringValue(e.Name, e.Value)
		k.Close()
		if err != nil {
			return "", err
		}
	}
	return "Origami Agent is installed. Keep this file where it is, then go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	title, _ := windows.UTF16PtrFromString("Origami Agent")
	text, _ := windows.UTF16PtrFromString(msg)
	_, _ = windows.MessageBox(0, text, title, 0x40) // MB_OK | MB_ICONINFORMATION
}
