.PHONY: all build-telegram build-lyrics build-all package package-telegram package-lyrics package-picard deploy clean

WASM_OUT := plugin.wasm
TELEGRAM_DIR := plugins/telegram-plugin
LYRICS_DIR := plugins/lyrics-plugin
PICARD_DIR := plugins/picard-auto-romanizer

TELEGRAM_NDP := navidrome-telegram.ndp
LYRICS_NDP := navidrome-lyrics-plugin.ndp
PICARD_ZIP := auto_romanizer.zip

all: package

build-telegram:
	@echo "==> Compiling telegram-plugin to WASM..."
	cd $(TELEGRAM_DIR) && tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

build-lyrics:
	@echo "==> Compiling lyrics-plugin to WASM..."
	cd $(LYRICS_DIR) && tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

build-all: build-telegram build-lyrics

package-telegram: build-telegram
	@echo "==> Packaging navidrome-telegram.ndp..."
	cd $(TELEGRAM_DIR) && zip -j $(TELEGRAM_NDP) manifest.json $(WASM_OUT)

package-lyrics: build-lyrics
	@echo "==> Packaging navidrome-lyrics-plugin.ndp..."
	cd $(LYRICS_DIR) && zip -j $(LYRICS_NDP) manifest.json $(WASM_OUT)

package-picard:
	@echo "==> Packaging auto_romanizer.zip for MusicBrainz Picard..."
	cd $(PICARD_DIR) && zip -r $(PICARD_ZIP) auto_romanizer

package: package-telegram package-lyrics package-picard
	@echo "Successfully packaged all plugins."

deploy:
	@bash scripts/deploy.sh $(DEST)

clean:
	@echo "Cleaning compiled WASM binaries, NDP packages, and Picard zip..."
	rm -f $(TELEGRAM_DIR)/$(WASM_OUT) $(TELEGRAM_DIR)/$(TELEGRAM_NDP)
	rm -f $(LYRICS_DIR)/$(WASM_OUT) $(LYRICS_DIR)/$(LYRICS_NDP)
	rm -f $(PICARD_DIR)/$(PICARD_ZIP)
