package main

import (
	"bufio"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
)

type datePattern struct {
	re *regexp.Regexp
}

var datePatterns = []datePattern{
	// ISO datetime: 2024-01-15T12:30:45 / 2024-01-15 12:30:45
	{regexp.MustCompile(`(?i)\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b`)},
	// ISO date only: 2024-01-15
	{regexp.MustCompile(`(?i)\b(\d{4}-\d{2}-\d{2})\b`)},
	// US / EU slash: 01/15/2024 or 15/01/2024
	{regexp.MustCompile(`(?i)\b(\d{1,2}/\d{1,2}/\d{4})\b`)},
	// US slash short: 01/15/24
	{regexp.MustCompile(`(?i)\b(\d{1,2}/\d{1,2}/\d{2})\b`)},
	// Month-name long: January 15, 2024 / Jan 15 2024
	{regexp.MustCompile(`(?i)\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b`)},
	// Compact: 20240115
	{regexp.MustCompile(`(?i)\b(20\d{6})\b`)},
}

func findDates(text string) []string {
	seen := make(map[string]bool)
	found := make([]string, 0)

	for _, p := range datePatterns {
		matches := p.re.FindAllStringSubmatch(text, -1)
		for _, m := range matches {
			if len(m) > 1 {
				val := m[1]
				if !seen[val] {
					seen[val] = true
					found = append(found, val)
				}
			}
		}
	}

	return found
}

func main() {
	logFile := `D:\app.txt`
	outputFile := `D:\go_err_out`

	f, err := os.Open(logFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERROR] File not found: %s\n", logFile)
		os.Exit(1)
	}
	defer f.Close()

	errorLines := 0
	noDateCount := 0
	dateCounts := make(map[string]int)

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.Contains(strings.ToLower(line), "error") {
			errorLines++
			dates := findDates(line)
			if len(dates) == 0 {
				noDateCount++
			} else {
				for _, d := range dates {
					dateCounts[d]++
				}
			}
		}
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "[ERROR] Failed reading %s: %v\n", logFile, err)
		os.Exit(1)
	}

	report := make([]string, 0)
	report = append(report, fmt.Sprintf("Scanning: %s", logFile))
	report = append(report, strings.Repeat("-", 60))

	if errorLines == 0 {
		report = append(report, "No lines containing 'error' were found.")
		_ = os.WriteFile(outputFile, []byte(strings.Join(report, "\n")+"\n"), 0644)
		fmt.Printf("Output written to: %s\n", outputFile)
		return
	}

	report = append(report, fmt.Sprintf("Total error lines : %d", errorLines))
	report = append(report, fmt.Sprintf("Lines without date: %d", noDateCount))
	report = append(report, "")

	sortedDates := make([]string, 0, len(dateCounts))
	for d := range dateCounts {
		sortedDates = append(sortedDates, d)
	}
	sort.Strings(sortedDates)

	if len(sortedDates) > 0 {
		colW := len("Date")
		for _, d := range sortedDates {
			if len(d) > colW {
				colW = len(d)
			}
		}

		report = append(report, fmt.Sprintf("%-*s   %s", colW, "Date", "Errors"))
		report = append(report, fmt.Sprintf("%s   %s", strings.Repeat("-", colW), strings.Repeat("-", 6)))

		totalWithDate := 0
		for _, d := range sortedDates {
			count := dateCounts[d]
			totalWithDate += count
			report = append(report, fmt.Sprintf("%-*s   %6d", colW, d, count))
		}

		report = append(report, fmt.Sprintf("%s   %s", strings.Repeat("-", colW), strings.Repeat("-", 6)))
		report = append(report, fmt.Sprintf("%-*s   %6d", colW, "TOTAL", totalWithDate))
	} else {
		report = append(report, "No dates detected on any error line.")
	}

	if err := os.WriteFile(outputFile, []byte(strings.Join(report, "\n")+"\n"), 0644); err != nil {
		fmt.Fprintf(os.Stderr, "[ERROR] Failed writing %s: %v\n", outputFile, err)
		os.Exit(1)
	}

	fmt.Printf("Output written to: %s\n", outputFile)
}
