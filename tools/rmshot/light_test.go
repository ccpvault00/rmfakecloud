package main

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func fakeLight(t *testing.T, level string) *Light {
	t.Helper()
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "brightness"), []byte(level+"\n"), 0o644)
	_ = os.WriteFile(filepath.Join(dir, "max_brightness"), []byte("2047\n"), 0o644)
	return &Light{Dir: dir}
}

func TestBlinkGoesFullThenOffAndRestores(t *testing.T) {
	l := fakeLight(t, "1040")
	if err := l.Blink(2); err != nil {
		t.Fatal(err)
	}
	if want := []int{2047, 0, 2047, 0, 1040}; !reflect.DeepEqual(l.written, want) {
		t.Fatalf("levels %v, want %v", l.written, want)
	}
}

func TestBlinkFlashesADarkLight(t *testing.T) {
	l := fakeLight(t, "0")
	if err := l.Blink(1); err != nil {
		t.Fatal(err)
	}
	if want := []int{2047, 0, 0}; !reflect.DeepEqual(l.written, want) {
		t.Fatalf("levels %v, want %v", l.written, want)
	}
}

func TestBlinkRestoresADimLight(t *testing.T) {
	l := fakeLight(t, "300")
	if err := l.Blink(1); err != nil {
		t.Fatal(err)
	}
	if want := []int{2047, 0, 300}; !reflect.DeepEqual(l.written, want) {
		t.Fatalf("levels %v, want %v", l.written, want)
	}
}

func TestBlinkWithoutALightFails(t *testing.T) {
	l := &Light{Dir: filepath.Join(t.TempDir(), "missing")}
	if err := l.Blink(1); err == nil {
		t.Fatal("no error for a missing front light")
	}
	var none *Light
	if err := none.Blink(3); err != nil {
		t.Fatal(err)
	}
}
