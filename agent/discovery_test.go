package main

import (
	"net"
	"testing"
)

func TestScannerFromRecord(t *testing.T) {
	sc, ok := scannerFromRecord("HP Envy 6000 [A1B2]",
		[]string{"txtvers=1", "ty=HP ENVY 6000", "uuid=1234-abcd", "rs=/eSCL"},
		[]net.IP{net.ParseIP("192.168.1.20")}, nil, 80, false)
	if !ok {
		t.Fatal("not ok")
	}
	want := Scanner{UUID: "1234-abcd", Name: "HP ENVY 6000", BaseURL: "http://192.168.1.20:80/eSCL"}
	if sc != want {
		t.Fatalf("got %+v", sc)
	}
}

func TestScannerFromRecordDefaultsAndIPv6(t *testing.T) {
	sc, ok := scannerFromRecord("Brother", nil, nil, []net.IP{net.ParseIP("fe80::1")}, 443, true)
	if !ok {
		t.Fatal("not ok")
	}
	if sc.UUID != "Brother" || sc.Name != "Brother" || sc.BaseURL != "https://[fe80::1]:443/eSCL" {
		t.Fatalf("got %+v", sc)
	}
}

func TestScannerFromRecordNeedsAddress(t *testing.T) {
	if _, ok := scannerFromRecord("x", nil, nil, nil, 80, false); ok {
		t.Fatal("record without address must be skipped")
	}
}
