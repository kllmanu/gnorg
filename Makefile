PREFIX ?= $(HOME)/.local
SCHEMA_DIR = $(PREFIX)/share/glib-2.0/schemas

run: schemas
	python3 src/vimtext.py

schemas: data/gschemas.compiled

data/gschemas.compiled: data/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas --strict data

install:
	install -Dm755 src/vimtext.py $(PREFIX)/bin/vimtext
	install -Dm644 data/io.github.kllmanu.Vimtext.desktop $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop
	install -Dm644 data/io.github.kllmanu.Vimtext.gschema.xml $(SCHEMA_DIR)/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

uninstall:
	rm -f $(PREFIX)/bin/vimtext $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop
	rm -f $(SCHEMA_DIR)/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

.PHONY: run schemas install uninstall
