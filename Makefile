.PHONY: all build test

DATE := $(shell command -v gdate 2>/dev/null || echo date)

all: build

build:
	TIME=$$($(DATE) --utc --iso-8601=minutes | cut -c1-16); \
	HASH=$$(git rev-parse HEAD | cut -c1-9); \
	go build -ldflags "-X main.gitHash=$$HASH -X main.buildTime=$$TIME" pptext.go

# End-to-end suite: runs the built binary over tests/fixtures/ and checks the
# generated report. Needs the binary beside scannos.txt and hebelist.txt, so it
# depends on build. Pass extra flags with ARGS, e.g. make test ARGS="-v -k dash".
test: build
	python3 tests/run_tests.py $(ARGS)
