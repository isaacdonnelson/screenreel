PREFIX ?= $(HOME)/.local

.PHONY: build install test

build:
	swift build -c release

install: build
	mkdir -p $(PREFIX)/bin
	cp .build/release/screenreel $(PREFIX)/bin/screenreel

test:
	swift run screenreel-check
