package main

import (
	"strings"
	"testing"
)

func TestDesktopEntry(t *testing.T) {
	d, err := desktopEntry("/home/u/Downloads/origami-agent-linux-amd64")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"Exec=\"/home/u/Downloads/origami-agent-linux-amd64\" %u",
		"MimeType=x-scheme-handler/origami-agent;",
		"NoDisplay=true",
	} {
		if !strings.Contains(d, want) {
			t.Errorf("missing %q in\n%s", want, d)
		}
	}
}

func TestWindowsEntries(t *testing.T) {
	got := windowsEntries(`C:\Users\u\Downloads\origami-agent.exe`)
	want := []regEntry{
		{`Software\Classes\origami-agent`, "", "URL:Origami Agent"},
		{`Software\Classes\origami-agent`, "URL Protocol", ""},
		{`Software\Classes\origami-agent\shell\open\command`, "", `"C:\Users\u\Downloads\origami-agent.exe" "%1"`},
	}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("entry %d: got %+v want %+v", i, got[i], want[i])
		}
	}
}

func TestDesktopEntryEscaping(t *testing.T) {
	d, err := desktopEntry("/a b/\"x$y`z\\w%u")
	if err != nil {
		t.Fatal(err)
	}
	want := "Exec=\"/a b/\\\\\"x\\\\$y\\\\`z\\\\\\\\w%%u\" %u"
	if !strings.Contains(d, want) {
		t.Errorf("want %s in\n%s", want, d)
	}
	if _, err := desktopEntry("/a\nExec=evil"); err == nil {
		t.Error("newline in path must be refused")
	}
}
