PREFIX ?= $(HOME)/.local

.PHONY: build install test

build:
	swift build -c release

install: build
	mkdir -p $(PREFIX)/bin
	cp .build/release/screenreel $(PREFIX)/bin/screenreel

test:
	python3 -m pytest -q tests
ifeq ($(shell command -v swift >/dev/null 2>&1 && echo yes),yes)
	swift run screenreel-check
endif
