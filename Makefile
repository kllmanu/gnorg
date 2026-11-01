PREFIX ?= $(HOME)/.local
APP_DIR = $(PREFIX)/share/organizer
SCHEMA_DIR = $(PREFIX)/share/glib-2.0/schemas

run: schemas
	python3 src/app.py

test:
	python3 -m unittest discover -s tests

schemas: data/gschemas.compiled

data/gschemas.compiled: data/io.github.kllmanu.Organizer.gschema.xml
	glib-compile-schemas --strict data

install:
	install -Dm644 -t $(APP_DIR) src/vim.py src/org.py src/agenda.py src/datepicker.py src/keybindings.py src/indent.py
	install -Dm755 src/app.py $(APP_DIR)/app.py
	install -Dm644 -t $(APP_DIR)/language-specs data/language-specs/*.lang
	install -Dm644 -t $(APP_DIR)/styles data/styles/*.xml
	install -d $(PREFIX)/bin
	ln -sf $(APP_DIR)/app.py $(PREFIX)/bin/organizer
	install -Dm644 data/io.github.kllmanu.Organizer.desktop $(PREFIX)/share/applications/io.github.kllmanu.Organizer.desktop
	install -Dm644 data/io.github.kllmanu.Organizer.gschema.xml $(SCHEMA_DIR)/io.github.kllmanu.Organizer.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

uninstall:
	rm -rf $(APP_DIR)
	rm -f $(PREFIX)/bin/organizer $(PREFIX)/share/applications/io.github.kllmanu.Organizer.desktop
	rm -f $(SCHEMA_DIR)/io.github.kllmanu.Organizer.gschema.xml
	glib-compile-schemas $(SCHEMA_DIR)

.PHONY: run test schemas install uninstall
