package main

import (
	"bufio"
	"os"
	"regexp"
	"testing"
)

// TestNoDuplicateRoutes scans main.go for mux.Handle / mux.HandleFunc registrations
// and fails if any route pattern appears more than once.
//
// Background: Go 1.25 ServeMux panics at startup when the same pattern is
// registered twice. This happened in Phase 21 when /api/guard/reset was
// registered by both ResetGuard (Phase 16 stub) and ServeGuardReset (Phase 19).
// This test ensures that regression cannot silently re-enter the codebase.
func TestNoDuplicateRoutes(t *testing.T) {
	f, err := os.Open("main.go")
	if err != nil {
		t.Fatalf("cannot open main.go: %v", err)
	}
	defer f.Close()

	// Match: mux.Handle("…") or mux.HandleFunc("…", …)
	re := regexp.MustCompile(`mux\.Handle(?:Func)?\("([^"]+)"`)

	seen := make(map[string]int) // pattern → first line number
	seenLine := make(map[string]int)

	scanner := bufio.NewScanner(f)
	lineNum := 0
	for scanner.Scan() {
		lineNum++
		line := scanner.Text()
		m := re.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		pattern := m[1]
		if prev, dup := seenLine[pattern]; dup {
			t.Errorf(
				"duplicate route %q: first registered at line %d, re-registered at line %d — "+
					"Go 1.25 ServeMux panics on startup when the same pattern is registered twice",
				pattern, prev, lineNum,
			)
		} else {
			seenLine[pattern] = lineNum
			seen[pattern]++
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("scanner error: %v", err)
	}

	t.Logf("checked %d unique route registrations, no duplicates found", len(seen))
}
