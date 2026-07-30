PLUGIN_NAME  := navidrome-telegram
WASM_OUT     := plugin.wasm
NDP_OUT      := $(PLUGIN_NAME).ndp

.PHONY: all build package clean

all: package

build: $(WASM_OUT)

$(WASM_OUT): main.go go.mod
	tinygo build -o $(WASM_OUT) -target wasip1 -buildmode=c-shared .

package: $(WASM_OUT)
	zip -j $(NDP_OUT) manifest.json $(WASM_OUT)
	@echo "Packaged: $(NDP_OUT)"

clean:
	rm -f $(WASM_OUT) $(NDP_OUT)
