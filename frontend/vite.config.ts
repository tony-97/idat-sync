import { defineConfig } from "vite";
import solidPlugin from "vite-plugin-solid";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [solidPlugin(), tailwindcss(), viteSingleFile()],
  build: {
    target: "es2022",
    outDir: "../src/idat_sync/ui",
    emptyOutDir: false,
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
  },
});
