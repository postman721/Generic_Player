PYTHON ?= python3
PREFIX ?= /usr/local
APPDIR ?= /opt/generic-player
DESTDIR ?=
INSTALL_PYTHON ?= /usr/bin/python3
PACKAGE_REVISION ?= 1

export PREFIX APPDIR DESTDIR INSTALL_PYTHON PACKAGE_REVISION

.PHONY: help run check install uninstall deb
.DEFAULT_GOAL := help

help:
	@printf '%s\n' \
	  'Generic Player source commands:' \
	  '  make run        Run from this source folder' \
	  '  make check      Check Python and media dependencies' \
	  '  make install    Install owned files (normally sudo make install)' \
	  '  make uninstall  Remove unchanged owned files only' \
	  '  make deb        Build an optional Debian package in dist/' \
	  '' \
	  'Manual install/uninstall use Python standard-library helpers; no package manager is required.' \
	  'make check works in a Git checkout and an extracted source release.' \
	  'Variables: PYTHON, PREFIX, APPDIR, DESTDIR, INSTALL_PYTHON, PACKAGE_REVISION.' \
	  'Defaults: code /opt/generic-player; command /usr/local/bin/generic-player.' \
	  'See README.md for Debian/Ubuntu, Arch, and Fedora dependencies.'

run:
	"$(PYTHON)" generic_player.py

check:
	"$(PYTHON)" scripts/check_dependencies.py

install:
	"$(PYTHON)" scripts/install.py install

uninstall:
	"$(PYTHON)" scripts/install.py uninstall

deb:
	"$(PYTHON)" scripts/build_deb.py
