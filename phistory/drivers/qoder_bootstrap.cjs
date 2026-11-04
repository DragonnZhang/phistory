"use strict";

const fs = require("node:fs");
const originalParse = JSON.parse;
const originalStringify = JSON.stringify;
const mode = process.env.PHISTORY_QODER_CAPTURE_BOOTSTRAP;
const reportPath = process.env.PHISTORY_QODER_CAPTURE_BOOTSTRAP_REPORT;

if (
  mode === "model-availability" &&
  reportPath &&
  process.env.CLAUDE_TAP_QODER_PROMPT_PATH
) {
  JSON.parse = function phistoryQoderBootstrap(...args) {
    const value = originalParse.apply(this, args);
    if (!value || !Array.isArray(value.assistant)) return value;
    const model = value.assistant.find(
      (entry) => entry && entry.key === "auto" && typeof entry.display_name === "string"
    );
    if (!model || ![false, 0, "0"].includes(model.enable)) return value;

    // Persist the mock condition before applying it; no account data is written.
    fs.writeFileSync(
      reportPath,
      originalStringify({ mode, model: "auto", original_enable: model.enable, enable: true }),
      { encoding: "utf8", mode: 0o600 }
    );
    model.enable = true;
    return value;
  };
}
