package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const HandoffAddr = "127.0.0.1:47811"

var errForeignServer = errors.New("this agent is paired with another Origami server; run it with --reset to change")

type LaunchParams struct{ Server, Token string }

type regEntry struct{ Key, Name, Value string }

func parseLaunchURL(raw string) (LaunchParams, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "origami-agent" || u.Host != "connect" {
		return LaunchParams{}, fmt.Errorf("not an origami-agent connect link")
	}
	q := u.Query()
	server, token := strings.TrimSuffix(q.Get("server"), "/"), q.Get("token")
	s, err := url.Parse(server)
	if err != nil || token == "" || s.Host == "" {
		return LaunchParams{}, fmt.Errorf("link is missing server or token")
	}
	local := s.Hostname() == "localhost" || s.Hostname() == "127.0.0.1"
	if s.Scheme != "https" && !(s.Scheme == "http" && local) {
		return LaunchParams{}, fmt.Errorf("server must use https")
	}
	return LaunchParams{Server: server, Token: token}, nil
}

func pinPath() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "origami-agent", "server"), nil
}

// checkPinned trusts the first server it sees and refuses any other afterwards.
func checkPinned(path, server string) error {
	server = strings.TrimSuffix(server, "/")
	data, err := os.ReadFile(path)
	if err == nil {
		if strings.TrimSpace(string(data)) != server {
			return errForeignServer
		}
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(server+"\n"), 0o600)
}

func listenSingleInstance() (net.Listener, error) { return net.Listen("tcp", HandoffAddr) }

func handOff(raw string) error {
	c, err := net.DialTimeout("tcp", HandoffAddr, 2*time.Second)
	if err != nil {
		return err
	}
	defer c.Close()
	_, err = fmt.Fprintln(c, raw)
	return err
}

func serveHandoffs(l net.Listener, onURL func(string)) {
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		go func(c net.Conn) {
			defer c.Close()
			_ = c.SetReadDeadline(time.Now().Add(5 * time.Second))
			if line, err := bufio.NewReader(c).ReadString('\n'); err == nil {
				onURL(strings.TrimSpace(line))
			}
		}(c)
	}
}

func validatedLaunch(raw string) (LaunchParams, error) {
	p, err := parseLaunchURL(raw)
	if err != nil {
		return p, err
	}
	path, err := pinPath()
	if err != nil {
		return p, err
	}
	return p, checkPinned(path, p.Server)
}

// launch is the shared entry for an origami-agent:// link on every OS.
func launch(raw string) {
	p, err := validatedLaunch(raw)
	if err != nil {
		notify("Origami Agent: " + err.Error())
		os.Exit(1)
	}
	l, err := listenSingleInstance()
	if err != nil {
		if handOff(raw) == nil {
			os.Exit(0) // the running agent takes over
		}
		notify("Origami Agent: cannot start: " + err.Error())
		os.Exit(1)
	}
	a := NewAgent(p.Server, p.Token, browseMDNS)
	go serveHandoffs(l, func(next string) {
		if q, err := validatedLaunch(next); err == nil {
			a.Relaunch(q.Server, q.Token)
		} else {
			log.Printf("ignored launch: %v", err)
		}
	})
	if err := a.Run(context.Background()); err != nil {
		log.Printf("agent stopped: %v", err)
	}
	os.Exit(0)
}

func reset() error {
	path, err := pinPath()
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func desktopEntry(exe string) string {
	return "[Desktop Entry]\n" +
		"Type=Application\n" +
		"Name=Origami Agent\n" +
		"Exec=\"" + exe + "\" %u\n" +
		"MimeType=x-scheme-handler/origami-agent;\n" +
		"NoDisplay=true\n" +
		"Terminal=false\n"
}

func windowsEntries(exe string) []regEntry {
	const base = `Software\Classes\origami-agent`
	return []regEntry{
		{base, "", "URL:Origami Agent"},
		{base, "URL Protocol", ""},
		{base + `\shell\open\command`, "", `"` + exe + `" "%1"`},
	}
}
