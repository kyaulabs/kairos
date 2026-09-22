/* Bot settings have one server-defined contract. This class never saves or starts trading. */
class SettingsForm {
  constructor(form, schema) {
    this.form = form;
    this.schema = schema;
    for (const [name, field] of Object.entries(schema.fields)) {
      if (field.editable === false) continue;
      const input = form.elements.namedItem(name);
      if (!input || !input.tagName) throw new Error(`Missing or duplicate settings control: ${name}`);
      if (field.choices) SettingsForm.choices(input, field.choices);
      if (['decimal', 'integer'].includes(field.type) && !field.choices) {
        input.type = 'number'; input.step = field.type === 'integer' ? '1' : 'any';
      }
      if (field.min !== undefined) input.min = field.min;
      if (field.max !== undefined) input.max = field.max;
      if (field.max_length !== undefined) input.maxLength = field.max_length;
      input.required = field.type !== 'boolean';
    }
  }
  static choices(input, choices, selected = input.value) {
    const options = Object.entries(choices).map(([value, label]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label; return option;
    });
    input.replaceChildren(...options);
    input.value = selected;
  }
  static applies(field, values) {
    return (!field.products || field.products.includes(values.product)) && (!field.strategies || field.strategies.includes(values.strategy));
  }
  load(saved) {
    for (const [name, field] of Object.entries(this.schema.fields)) {
      if (field.editable === false) continue;
      const input = this.form.elements.namedItem(name), value = saved[name];
      if (field.type === 'boolean') input.checked = value;
      else input.value = field.positive && Number(value) === 0 ? '' : value;
    }
    this.update();
  }
  update() {
    const values = {product: this.form.elements.namedItem('product').value, strategy: this.form.elements.namedItem('strategy').value};
    for (const [name, field] of Object.entries(this.schema.fields)) {
      if (field.editable === false) continue;
      const input = this.form.elements.namedItem(name);
      const active = SettingsForm.applies(field, values);
      // An incompatible enabled switch stays visible until the operator explicitly turns it off.
      input.disabled = !active && !(field.must_be_off_when_inactive && input.checked);
      const label = input.closest('label');
      if (label) label.hidden = input.disabled;
      if (field.positive) input.setCustomValidity(active && !(Number(input.value) > 0) ? 'Enter an explicit positive limit price.' : '');
      if (field.must_be_off_when_inactive) input.setCustomValidity(!active && input.checked ? 'Turn off spot-only capital recovery before selecting another product.' : '');
    }
    for (const group of this.form.querySelectorAll('[data-settings-group]')) {
      group.hidden = [...group.querySelectorAll('input, select')].every(input => input.disabled);
    }
    const strategy = this.form.elements.namedItem('strategy');
    for (const option of strategy.options) {
      const definition = this.schema.strategies[option.value];
      option.disabled = !definition.products.includes(values.product);
      option.textContent = definition.label + (option.disabled ? ' · unavailable for this product' : '');
    }
    strategy.setCustomValidity(this.schema.strategies[values.strategy]?.products.includes(values.product) ? '' : 'Choose a strategy supported by this product.');
  }
  values(saved) {
    const values = {...saved};
    for (const [name, field] of Object.entries(this.schema.fields)) {
      if (field.editable === false) continue;
      const input = this.form.elements.namedItem(name);
      // Inactive drafts must not resize another portfolio or change another strategy's settings.
      if (input.disabled && !field.must_be_off_when_inactive) continue;
      values[name] = field.type === 'boolean' ? input.checked : field.type === 'integer' ? Number(input.value) : input.value;
    }
    return values;
  }
}
window.SettingsForm = SettingsForm;
