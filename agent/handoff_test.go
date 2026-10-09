package main

import (
	"bufio"
	"fmt"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func newKey(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "origami-agent", "handoff.key")
	if err := writeHandoffKey(p); err != nil {
		t.Fatal(err)
	}
	return p
}

func listen(t *testing.T) net.Listener {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	return l
}

func TestHandoff(t *testing.T) {
	key := newKey(t)
	l := listen(t)
	got := make(chan string, 1)
	go serveHandoffs(l, key, func(u string) { got <- u })
	if err := handOffTo(l.Addr().String(), key, "origami-agent://connect?x=1"); err != nil {
		t.Fatal(err)
	}
	select {
	case u := <-got:
		if u != "origami-agent://connect?x=1" {
			t.Fatalf("got %q", u)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no url delivered")
	}
}

// A listener without the right key must never see the URL.
func TestHandoffRefusesWrongReceiver(t *testing.T) {
	l := listen(t)
	seen := make(chan string, 1)
	go func() {
		c, err := l.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		r := bufio.NewReader(c)
		_, _ = r.ReadString('\n')
		fmt.Fprint(c, "deadbeef\ncafebabe\n")
		var sb strings.Builder
		_ = c.SetReadDeadline(time.Now().Add(time.Second))
		for {
			line, err := r.ReadString('\n')
			sb.WriteString(line)
			if err != nil {
				break
			}
		}
		seen <- sb.String()
	}()
	if err := handOffTo(l.Addr().String(), newKey(t), "origami-agent://connect?secret=1"); err == nil {
		t.Fatal("want error")
	}
	if s := <-seen; s != "" {
		t.Fatalf("receiver saw data after failed auth: %q", s)
	}
}

func TestHandoffReceiverRejectsWrongSender(t *testing.T) {
	l := listen(t)
	called := make(chan string, 1)
	go serveHandoffs(l, newKey(t), func(u string) { called <- u })
	if err := handOffTo(l.Addr().String(), newKey(t), "origami-agent://connect?x=1"); err == nil {
		t.Fatal("want error")
	}
	// A raw client that skips verification and sends a MAC from the wrong key.
	c, err := net.Dial("tcp", l.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	r := bufio.NewReader(c)
	fmt.Fprintln(c, "00")
	_, _ = r.ReadString('\n')
	_, _ = r.ReadString('\n')
	fmt.Fprint(c, "badmac\norigami-agent://connect?x=1\n")
	line, _ := r.ReadString('\n')
	if strings.TrimSpace(line) == "ok" {
		t.Fatal("receiver acked a bad sender")
	}
	select {
	case u := <-called:
		t.Fatalf("onURL called with %q", u)
	case <-time.After(300 * time.Millisecond):
	}
}
