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

func TestScannerFromRecordSkipsLocalAddresses(t *testing.T) {
	ips := []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("0.0.0.0"), net.ParseIP("224.0.0.251"), net.ParseIP("192.168.1.20")}
	sc, ok := scannerFromRecord("x", []string{"uuid=u1"}, ips, nil, 80, false)
	if !ok || sc.BaseURL != "http://192.168.1.20:80/eSCL" {
		t.Fatalf("got %+v %v", sc, ok)
	}
	sc, ok = scannerFromRecord("x", nil, []net.IP{net.ParseIP("127.0.0.1")}, []net.IP{net.ParseIP("::1"), net.ParseIP("fe80::1")}, 80, false)
	if !ok || sc.BaseURL != "http://[fe80::1]:80/eSCL" {
		t.Fatalf("got %+v %v", sc, ok)
	}
	if _, ok := scannerFromRecord("x", nil, []net.IP{net.ParseIP("127.0.0.1")}, []net.IP{net.ParseIP("::"), net.ParseIP("ff02::fb")}, 80, false); ok {
		t.Fatal("record with only loopback, unspecified or multicast addresses must be skipped")
	}
}

func TestScannerFromRecordInvalidRootFallsBack(t *testing.T) {
	for _, rs := range []string{"rs=../admin", "rs=a?b", "rs=x#y", "rs=a b"} {
		sc, ok := scannerFromRecord("x", []string{rs}, []net.IP{net.ParseIP("192.168.1.20")}, nil, 80, false)
		if !ok || sc.BaseURL != "http://192.168.1.20:80/eSCL" {
			t.Fatalf("%s: got %+v", rs, sc)
		}
	}
	sc, _ := scannerFromRecord("x", []string{"rs=/scan/eSCL/"}, []net.IP{net.ParseIP("192.168.1.20")}, nil, 80, false)
	if sc.BaseURL != "http://192.168.1.20:80/scan/eSCL" {
		t.Fatalf("got %+v", sc)
	}
}
