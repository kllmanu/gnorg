PREFIX ?= $(HOME)/.local
APP_DIR = $(PREFIX)/share/vimtext
SCHEMA_DIR = $(PREFIX)/share/glib-2.0/schemas

run: schemas
	python3 src/vimtext.py

test:
	python3 -m unittest discover -s tests

schemas: data/gschemas.compiled

data/gschemas.compiled: data/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas --strict data

install:
	install -Dm644 -t $(APP_DIR) src/org.py src/agenda.py src/datepicker.py
	install -Dm755 src/vimtext.py $(APP_DIR)/vimtext.py
	install -Dm644 -t $(APP_DIR)/language-specs data/language-specs/*.lang
	install -Dm644 -t $(APP_DIR)/styles data/styles/*.xml
	install -d $(PREFIX)/bin
	ln -sf $(APP_DIR)/vimtext.py $(PREFIX)/bin/vimtext
	install -Dm644 data/io.github.kllmanu.Vimtext.desktop $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop
	install -Dm644 data/io.github.kllmanu.Vimtext.gschema.xml $(SCHEMA_DIR)/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

uninstall:
	rm -rf $(APP_DIR)
	rm -f $(PREFIX)/bin/vimtext $(PREFIX)/share/applications/io.github.kllmanu.Vimtext.desktop
	rm -f $(SCHEMA_DIR)/io.github.kllmanu.Vimtext.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

.PHONY: run test schemas install uninstall
