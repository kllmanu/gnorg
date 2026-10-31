PREFIX ?= $(HOME)/.local

run:
	python3 src/vimtext.py

install:
	install -Dm755 src/vimtext.py $(PREFIX)/bin/vimtext
	install -Dm644 data/io.github.kllmanu.Vimtext.desktop $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop

uninstall:
	rm -f $(PREFIX)/bin/vimtext $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop

.PHONY: run install uninstall
