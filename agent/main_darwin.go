//go:build darwin

package main

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework Cocoa
void runApp(void);
*/
import "C"

import (
	"fmt"
	"log"
	"os"
	"runtime"
	"strings"
	"time"
)

var urls = make(chan string, 4)

func init() { runtime.LockOSThread() } // Cocoa must run on the main thread

//export goHandleURL
func goHandleURL(u *C.char) {
	select {
	case urls <- C.GoString(u):
	default: // queue full: drop rather than block the Cocoa main thread
	}
}

func main() {
	args := os.Args[1:]
	switch {
	case len(args) == 1 && args[0] == "--version":
		fmt.Println(Version)
		return
	case len(args) == 1 && args[0] == "--reset":
		if err := reset(); err != nil {
			notify("Origami Agent: " + err.Error())
			os.Exit(1)
		}
		notify("Origami Agent: server pairing cleared.")
		return
	case len(args) == 0 || (len(args) == 1 && strings.HasPrefix(args[0], "-psn_")): // Apple Event flow
	default:
		fmt.Fprintln(os.Stderr, "usage: origami-agent [--reset | --version]")
		os.Exit(2)
	}
	go func() {
		select {
		case u := <-urls:
			go func() { // later links reach this running app as Apple Events
				for next := range urls {
					keyPath, err := handoffKeyPath()
					if err == nil {
						err = handOffTo(HandoffAddr, keyPath, next)
					}
					if err != nil {
						log.Printf("hand-off failed: %v", err)
					}
				}
			}()
			if strings.HasPrefix(strings.ToLower(u), "origami-agent:") {
				launch(u) // exits the process when the agent stops
			}
			os.Exit(0)
		case <-time.After(3 * time.Second): // opened by double-click: registration only
			msg, err := register()
			if err != nil {
				notify("Origami Agent: registration failed: " + err.Error())
				os.Exit(1)
			}
			notify(msg)
			os.Exit(0)
		}
	}()
	C.runApp()
}
