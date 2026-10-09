//go:build !darwin

package main

import (
	"fmt"
	"os"
	"strings"
)

func main() {
	args := os.Args[1:]
	switch {
	case len(args) == 1 && strings.HasPrefix(strings.ToLower(args[0]), "origami-agent:"):
		launch(args[0])
	case len(args) == 1 && args[0] == "--version":
		fmt.Println(Version)
	case len(args) == 1 && args[0] == "--reset":
		if err := reset(); err != nil {
			notify("Origami Agent: " + err.Error())
			os.Exit(1)
		}
		notify("Origami Agent: server pairing cleared.")
	case len(args) == 0 || (len(args) == 1 && args[0] == "--register"): // double-click or --register
		msg, err := register()
		if err != nil {
			notify("Origami Agent: registration failed: " + err.Error())
			os.Exit(1)
		}
		notify(msg)
	default:
		fmt.Fprintln(os.Stderr, "usage: origami-agent [--register | --reset | --version | origami-agent://connect?...]")
		os.Exit(2)
	}
}
