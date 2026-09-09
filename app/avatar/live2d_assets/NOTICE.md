# Vendored third-party assets

- **`haru_greeter_t03/`** — Live2D's official free sample model "Haru",
  redistributed under the [Live2D Free Material
  License](https://www.live2d.com/en/download/sample-data/), sourced from
  [guansss/pixi-live2d-display](https://github.com/guansss/pixi-live2d-display)'s
  test fixtures (MIT-licensed repo). Widely used as the standard demo model
  across the Live2D web ecosystem (open-LLM-VTuber and most
  pixi-live2d-display tutorials use the same sample).
- **`mao_pro/`** — Live2D's official free sample model "Niziiro Mao (PRO
  Version)", under the same Live2D Free Material License (see the
  bundled `ReadMe.txt`'s license section), sourced from
  [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)'s
  bundled sample models. Texture downscaled from the original 4096x4096
  to 2048x2048 (8.2MB -> 2.6MB) - plenty for a ~240px on-screen avatar.
- **`js/pixi.min.js`** — [PixiJS](https://pixijs.com/) v6.5.10, MIT license.
- **`js/cubism4.min.js`** — [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display)
  v0.4.0, MIT license.
- **`js/live2dcubismcore.min.js`** — Live2D Cubism Core (proprietary
  runtime), fetched from Live2D's own official CDN
  (`cubism.live2d.com/sdk-web/cubismcore/`), which is the standard,
  Live2D-sanctioned way every Cubism 4 web app bundles this file.
