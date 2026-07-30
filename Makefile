.PHONY: all build-telegram build-lyrics build-metadata build-all package package-telegram package-lyrics package-metadata package-picard package-deduplicator deploy-telegram deploy-lyrics deploy-metadata deploy clean

# Cross-platform file removal (one file per call)
ifeq ($(OS),Windows_NT)
    RM_F = powershell -Command "Remove-Item -Force -ErrorAction SilentlyContinue -Path '$(1)'"
else
    RM_F = rm -f $(1)
endif

WASM_OUT := plugin.wasm
TELEGRAM_DIR := plugins/telegram-plugin
LYRICS_DIR := plugins/lyrics-plugin
METADATA_DIR := plugins/nd-metadata
PICARD_DIR := plugins/picard-auto-romanizer
DEDUP_DIR := plugins/picard-deduplicator

TELEGRAM_NDP := navidrome-telegram.ndp
LYRICS_NDP := nd-lyrics.ndp
METADATA_NDP := nd-metadata.ndp
PICARD_ZIP := auto_romanizer.zip
DEDUP_ZIP := picard_deduplicator.zip

-include .env

DEST ?= $(NAV_DEST)
DEST ?= user@your-server:/path/to/plugins


all: package

build-telegram:
	@echo "==> Compiling telegram-plugin to WASM..."
	cd $(TELEGRAM_DIR) && tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

build-lyrics:
	@echo "==> Compiling lyrics-plugin to WASM..."
	cd $(LYRICS_DIR) && tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

build-metadata:
	@echo "==> Compiling nd-metadata plugin to WASM..."
	cd $(METADATA_DIR) && tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

build-all: build-telegram build-lyrics build-metadata

package-telegram: build-telegram
	@echo "==> Packaging navidrome-telegram.ndp..."
	cd $(TELEGRAM_DIR) && zip -j $(TELEGRAM_NDP) manifest.json $(WASM_OUT)

package-lyrics: build-lyrics
	@echo "==> Packaging nd-lyrics.ndp..."
	cd $(LYRICS_DIR) && zip -j $(LYRICS_NDP) manifest.json $(WASM_OUT)

package-metadata: build-metadata
	@echo "==> Packaging nd-metadata.ndp..."
	cd $(METADATA_DIR) && zip -j $(METADATA_NDP) manifest.json $(WASM_OUT)

package-picard:
	@echo "==> Packaging auto_romanizer.zip for MusicBrainz Picard..."
	cd $(PICARD_DIR) && zip -r $(PICARD_ZIP) auto_romanizer

package-deduplicator:
	@echo "==> Packaging picard_deduplicator.zip for MusicBrainz Picard..."
	cd $(DEDUP_DIR) && zip -r $(DEDUP_ZIP) picard_deduplicator

package: package-telegram package-lyrics package-metadata package-picard package-deduplicator
	@echo "Successfully packaged all plugins."

deploy-telegram: package-telegram
	@echo "==> Deploying telegram-plugin to $(DEST)..."
	scp $(TELEGRAM_DIR)/$(TELEGRAM_NDP) "$(DEST)/$(TELEGRAM_NDP)"

deploy-lyrics: package-lyrics
	@echo "==> Deploying lyrics-plugin to $(DEST)..."
	scp $(LYRICS_DIR)/$(LYRICS_NDP) "$(DEST)/$(LYRICS_NDP)"

deploy-metadata: package-metadata
	@echo "==> Deploying nd-metadata plugin to $(DEST)..."
	scp $(METADATA_DIR)/$(METADATA_NDP) "$(DEST)/$(METADATA_NDP)"

deploy: package
	scp plugins/telegram-plugin/$(TELEGRAM_NDP) $(DEST)/$(TELEGRAM_NDP)
	scp plugins/lyrics-plugin/$(LYRICS_NDP) $(DEST)/$(LYRICS_NDP)
	scp plugins/nd-metadata/$(METADATA_NDP) $(DEST)/$(METADATA_NDP)
	@echo "==> Deploy completo."

clean:
	@echo "Cleaning compiled WASM binaries, NDP packages, and Picard zips..."
	$(call RM_F,$(TELEGRAM_DIR)/$(WASM_OUT))
	$(call RM_F,$(TELEGRAM_DIR)/$(TELEGRAM_NDP))
	$(call RM_F,$(LYRICS_DIR)/$(WASM_OUT))
	$(call RM_F,$(LYRICS_DIR)/$(LYRICS_NDP))
	$(call RM_F,$(METADATA_DIR)/$(WASM_OUT))
	$(call RM_F,$(METADATA_DIR)/$(METADATA_NDP))
	$(call RM_F,$(PICARD_DIR)/$(PICARD_ZIP))
	$(call RM_F,$(DEDUP_DIR)/$(DEDUP_ZIP))

