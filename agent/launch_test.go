package main

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestParseLaunchURL(t *testing.T) {
	p, err := parseLaunchURL("origami-agent://connect?server=https%3A%2F%2Forigami.example.org&token=abc")
	if err != nil || p.Server != "https://origami.example.org" || p.Token != "abc" {
		t.Fatalf("got %+v %v", p, err)
	}
	for _, bad := range []string{
		"origami-agent://other?server=https%3A%2F%2Fx&token=a",
		"origami-agent://connect?server=ftp%3A%2F%2Fx&token=a",
		"origami-agent://connect?server=http%3A%2F%2Fexample.org&token=a", // plain http only for localhost
		"origami-agent://connect?server=https%3A%2F%2Fx",
		"https://connect?server=https%3A%2F%2Fx&token=a",
	} {
		if _, err := parseLaunchURL(bad); err == nil {
			t.Errorf("%s: want error", bad)
		}
	}
	if _, err := parseLaunchURL("origami-agent://connect?server=http%3A%2F%2Flocalhost%3A8000&token=a"); err != nil {
		t.Errorf("localhost http must be allowed: %v", err)
	}
}

func TestCheckPinned(t *testing.T) {
	path := filepath.Join(t.TempDir(), "origami-agent", "server")
	if err := checkPinned(path, "https://a.example"); err != nil {
		t.Fatal(err)
	}
	if err := checkPinned(path, "https://a.example/"); err != nil {
		t.Fatalf("same server with slash: %v", err)
	}
	if err := checkPinned(path, "https://evil.example"); !errors.Is(err, errForeignServer) {
		t.Fatalf("want errForeignServer, got %v", err)
	}
}

func TestCheckPinnedUnreadable(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "server")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := checkPinned(dir, "https://a.example"); err == nil || errors.Is(err, errForeignServer) {
		t.Fatalf("want read error, got %v", err)
	}
	if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
		t.Fatal("pin path must not be overwritten")
	}
}
