package main

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/coder/websocket"
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
	if !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(server+"\n"), 0o600)
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
	keyPath, err := handoffKeyPath()
	if err != nil {
		notify("Origami Agent: " + err.Error())
		os.Exit(1)
	}
	l, err := listenSingleInstance()
	if err != nil {
		if handOffTo(HandoffAddr, keyPath, raw) == nil {
			os.Exit(0) // the running agent takes over
		}
		notify("Origami Agent: port 47811 is in use by another program or user")
		os.Exit(1)
	}
	if err := writeHandoffKey(keyPath); err != nil {
		notify("Origami Agent: cannot start: " + err.Error())
		os.Exit(1)
	}
	a := NewAgent(p.Server, p.Token, browseMDNS)
	go serveHandoffs(l, keyPath, func(next string) {
		if q, err := validatedLaunch(next); err == nil {
			a.Relaunch(q.Server, q.Token)
			go notify("Origami Agent: switched to a new Origami session")
		} else {
			log.Printf("ignored launch: %v", err)
		}
	})
	if err := a.Run(context.Background()); err != nil {
		log.Printf("agent stopped: %v", err)
		if msg := exitNotice(err); msg != "" {
			notify(msg)
		}
	}
	os.Exit(0)
}

// exitNotice is what the user is told when Run ends; "" for a quiet exit.
func exitNotice(err error) string {
	if websocket.CloseStatus(err) == statusBadToken {
		return "Origami Agent: the link expired — search for local scanners again in Origami."
	}
	return ""
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

// desktopEntry renders the .desktop file. The executable path is quoted and escaped
// per the Desktop Entry spec (Exec quoting, then string-value escaping, % as %%).
func desktopEntry(exe string) (string, error) {
	if strings.ContainsAny(exe, "\n\r") {
		return "", fmt.Errorf("executable path contains a newline")
	}
	var q strings.Builder
	for _, r := range exe {
		switch r {
		case '\\':
			q.WriteString(`\\\\`) // Exec escape (\\), doubled again for the string value
		case '"', '`', '$':
			q.WriteString(`\\`) // Exec escape (\), doubled for the string value
			q.WriteRune(r)
		case '%':
			q.WriteString("%%")
		default:
			q.WriteRune(r)
		}
	}
	return "[Desktop Entry]\n" +
		"Type=Application\n" +
		"Name=Origami Agent\n" +
		"Exec=\"" + q.String() + "\" %u\n" +
		"MimeType=x-scheme-handler/origami-agent;\n" +
		"NoDisplay=true\n" +
		"Terminal=false\n", nil
}

func windowsEntries(exe string) []regEntry {
	const base = `Software\Classes\origami-agent`
	return []regEntry{
		{base, "", "URL:Origami Agent"},
		{base, "URL Protocol", ""},
		{base + `\shell\open\command`, "", `"` + exe + `" "%1"`},
	}
}
