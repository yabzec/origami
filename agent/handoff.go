package main

import (
	"bufio"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// The hand-off port is reachable by every local user, so both sides prove they hold
// a per-user key (handoff.key) before anything secret is sent:
//
//	S -> R: nonceS
//	R -> S: HMAC(key, "recv"+nonceS), nonceR
//	S -> R: HMAC(key, "send"+nonceR), url   (only after S verified R)
//	R -> S: ok
const (
	handoffMaxBytes = 8 << 10
	handoffTimeout  = 5 * time.Second
	handoffKeyLen   = 32
)

func handoffKeyPath() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "origami-agent", "handoff.key"), nil
}

// writeHandoffKey creates (or replaces) the per-user key. Called by the listening instance.
func writeHandoffKey(path string) error {
	key := make([]byte, handoffKeyLen)
	if _, err := rand.Read(key); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, key, 0o600)
}

func readHandoffKey(path string) ([]byte, error) {
	key, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(key) != handoffKeyLen {
		return nil, errors.New("invalid hand-off key")
	}
	return key, nil
}

func handoffMAC(key []byte, label string, nonce string) string {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(label + nonce))
	return hex.EncodeToString(m.Sum(nil))
}

func macEqual(a, b string) bool { return hmac.Equal([]byte(a), []byte(b)) }

func newNonce() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

func readLine(r *bufio.Reader) (string, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(line), nil
}

func listenSingleInstance() (net.Listener, error) { return net.Listen("tcp", HandoffAddr) }

// handOffTo delivers raw to the instance listening on addr, after mutual authentication.
func handOffTo(addr, keyPath, raw string) error {
	key, err := readHandoffKey(keyPath)
	if err != nil {
		return err
	}
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return err
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(handoffTimeout))
	r := bufio.NewReader(io.LimitReader(c, handoffMaxBytes))
	nonceS, err := newNonce()
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintln(c, nonceS); err != nil {
		return err
	}
	macR, err := readLine(r)
	if err != nil {
		return err
	}
	nonceR, err := readLine(r)
	if err != nil {
		return err
	}
	if !macEqual(macR, handoffMAC(key, "recv", nonceS)) {
		return errors.New("hand-off peer failed authentication")
	}
	if _, err := fmt.Fprintf(c, "%s\n%s\n", handoffMAC(key, "send", nonceR), raw); err != nil {
		return err
	}
	ack, err := readLine(r)
	if err != nil {
		return err
	}
	if ack != "ok" {
		return errors.New("hand-off rejected")
	}
	return nil
}

func serveHandoffs(l net.Listener, keyPath string, onURL func(string)) {
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		go func(c net.Conn) {
			defer c.Close()
			if raw, ok := receiveHandoff(c, keyPath); ok {
				onURL(raw)
			}
		}(c)
	}
}

func receiveHandoff(c net.Conn, keyPath string) (string, bool) {
	_ = c.SetDeadline(time.Now().Add(handoffTimeout))
	key, err := readHandoffKey(keyPath)
	if err != nil {
		return "", false
	}
	r := bufio.NewReader(io.LimitReader(c, handoffMaxBytes))
	nonceS, err := readLine(r)
	if err != nil || nonceS == "" {
		return "", false
	}
	nonceR, err := newNonce()
	if err != nil {
		return "", false
	}
	if _, err := fmt.Fprintf(c, "%s\n%s\n", handoffMAC(key, "recv", nonceS), nonceR); err != nil {
		return "", false
	}
	macS, err := readLine(r)
	if err != nil || !macEqual(macS, handoffMAC(key, "send", nonceR)) {
		return "", false
	}
	raw, err := readLine(r)
	if err != nil {
		return "", false
	}
	_, _ = fmt.Fprintln(c, "ok")
	return raw, true
}
