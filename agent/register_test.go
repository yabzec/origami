package main

import (
	"strings"
	"testing"
)

func TestDesktopEntry(t *testing.T) {
	d := desktopEntry("/home/u/Downloads/origami-agent-linux-amd64")
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
