import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

class FakeElement {
  constructor() {
    this.hidden = false;
    this.disabled = false;
    this.attributes = new Map();
    this.listeners = new Map();
    this.focused = false;
    this.children = new Map();
    this.queryAll = new Map();
  }
  addEventListener(type, callback) {
    const values = this.listeners.get(type) || [];
    values.push(callback);
    this.listeners.set(type, values);
  }
  dispatch(type, values = {}) {
    for (const callback of this.listeners.get(type) || []) {
      callback({ target: this, ...values });
    }
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  querySelector(selector) { return this.children.get(selector) ?? null; }
  querySelectorAll(selector) { return this.queryAll.get(selector) ?? []; }
  focus() { this.focused = true; }
  contains(target) { return target === this || [...this.children.values()].includes(target); }
}

class FakeDialog extends FakeElement {
  constructor() { super(); this.open = false; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
class FakeSelect extends FakeElement { constructor() { super(); this.value = ""; } }

globalThis.HTMLElement = FakeElement;
globalThis.HTMLDialogElement = FakeDialog;
globalThis.HTMLSelectElement = FakeSelect;

const toggle = new FakeElement();
toggle.setAttribute("aria-expanded", "false");
const panel = new FakeElement();
panel.hidden = true;
const newOffer = new FakeElement();
panel.children.set('[role="menuitem"]', newOffer);
const menuRoot = new FakeElement();
menuRoot.children.set("[data-bonus-offer-menu-toggle]", toggle);
menuRoot.children.set("[data-bonus-offer-menu-panel]", panel);
menuRoot.contains = (target) => [menuRoot, toggle, panel, newOffer].includes(target);

const dialog = new FakeDialog();
const promotionType = new FakeSelect();
promotionType.value = "qualifying_bet";
const firstField = promotionType;
dialog.children.set("select, input, textarea, button", firstField);
dialog.children.set('[name="promotion_type"]', promotionType);

const qualifyingInput = new FakeElement();
const freeInput = new FakeElement();
const qualifying = new FakeElement();
const free = new FakeElement();
qualifying.setAttribute("data-bonus-offer-strategy", "qualifying_bet");
free.setAttribute("data-bonus-offer-strategy", "free_bet");
qualifying.queryAll.set("input, select, textarea", [qualifyingInput]);
free.queryAll.set("input, select, textarea", [freeInput]);
dialog.queryAll.set("[data-bonus-offer-strategy]", [qualifying, free]);

const close = new FakeElement();
dialog.queryAll.set("[data-bonus-offer-dialog-close]", [close]);

const documentListeners = new Map();
globalThis.document = {
  querySelector(selector) {
    if (selector === "[data-bonus-offer-menu]") return menuRoot;
    if (selector === "[data-bonus-offer-dialog]") return dialog;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-bonus-offer-dialog-open]") return [newOffer];
    return [];
  },
  addEventListener(type, callback) {
    const values = documentListeners.get(type) || [];
    values.push(callback);
    documentListeners.set(type, values);
  },
};
const dispatchDocument = (type, event) => {
  for (const callback of documentListeners.get(type) || []) callback(event);
};

const source = fs.readFileSync(process.argv[2], "utf8");
vm.runInThisContext(source, { filename: process.argv[2] });

toggle.dispatch("click");
assert.equal(panel.hidden, false);
assert.equal(toggle.getAttribute("aria-expanded"), "true");
assert.equal(newOffer.focused, true);

dispatchDocument("keydown", { key: "Escape", target: toggle });
assert.equal(panel.hidden, true);
assert.equal(toggle.getAttribute("aria-expanded"), "false");
assert.equal(toggle.focused, true);

newOffer.dispatch("click");
assert.equal(dialog.open, true);
assert.equal(firstField.focused, true);

close.dispatch("click");
assert.equal(dialog.open, false);

assert.equal(qualifying.hidden, false);
assert.equal(qualifyingInput.disabled, false);
assert.equal(free.hidden, true);
assert.equal(freeInput.disabled, true);

promotionType.value = "free_bet";
promotionType.dispatch("change");
assert.equal(qualifying.hidden, true);
assert.equal(qualifyingInput.disabled, true);
assert.equal(free.hidden, false);
assert.equal(freeInput.disabled, false);
