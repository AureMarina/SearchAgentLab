/** Read-only summaries of recorded training fields. No reconstructed rewards or tokens. */
export const isNumber = value => typeof value === 'number' && Number.isFinite(value);

export function trainingNumber(value) {
  if (!isNumber(value)) return '未记录';
  if (value !== 0 && Math.abs(value) < .001) return value.toExponential(3);
  return Number(value.toPrecision(7)).toString();
}

export function metricChoices(rows = []) {
  const keys = [...new Set(rows.flatMap(row => Object.keys(row).filter(key => key !== 'step' && isNumber(row[key]))))];
  const validation = key => /(^|[\/_.-])(eval(?:uation)?|val(?:idation)?|test)(?=[\/_.-]|$)/i.test(key);
  return {
    keys,
    loss: keys.filter(key => /loss/i.test(key) && !validation(key)),
    reward: keys.filter(key => /reward/i.test(key) && !validation(key)),
    validation: keys.filter(validation),
    extra: keys.filter(key => !validation(key) && !/(loss|reward)/i.test(key)),
  };
}

export function summarizeGroup(samples = []) {
  const rewards = samples.map(sample => sample.reward).filter(isNumber);
  const advantages = samples.map(sample => sample.advantage).filter(isNumber);
  return {
    sample_count: samples.length,
    reward: {
      recorded: rewards.length,
      complete: samples.length > 0 && rewards.length === samples.length,
      min: rewards.length ? Math.min(...rewards) : null,
      max: rewards.length ? Math.max(...rewards) : null,
      mean: rewards.length ? rewards.reduce((a, b) => a + b, 0) / rewards.length : null,
      variation: rewards.length < 2 ? 'insufficient' : Math.min(...rewards) === Math.max(...rewards) ? 'uniform' : 'mixed',
    },
    advantage: {
      recorded: advantages.length,
      nonzero: advantages.filter(value => value !== 0).length,
      positive: advantages.filter(value => value > 0).length,
      negative: advantages.filter(value => value < 0).length,
      zero: advantages.filter(value => value === 0).length,
    },
  };
}

export function recordedTokenFields(sample = {}) {
  const parts = [Array.isArray(sample.input_ids) ? `${sample.input_ids.length} input_ids` : Array.isArray(sample.token_ids) ? `${sample.token_ids.length} token_ids`
    : Array.isArray(sample.response_ids) ? `${sample.response_ids.length} response_ids` : 'Token 未记录'];
  for (const key of ['response_mask', 'loss_mask']) {
    if (!Array.isArray(sample[key])) continue;
    const values = sample[key];
    parts.push(values.every(value => value === 0 || value === 1)
      ? `${key} ${values.filter(value => value === 1).length}/${values.length}` : `${key} ${values.length} 项`);
  }
  if (!Array.isArray(sample.response_mask) && !Array.isArray(sample.loss_mask)) parts.push('mask 未记录');
  return parts.join(' · ');
}

export function trainingTraceParams({project, track, experiment, run, panel, checkpoint, baseline, selections, batch, group, sample, index}) {
  const ref = sample.trace_ref || {};
  const source = ref.object_id ? {method: ref.object_id} : ref.trajectory ? {trajectory: ref.trajectory}
    : ref.file ? {file: ref.file, record: ref.record}
      : ['steps', 'messages', 'trajectories'].some(key => Array.isArray(sample[key])) ? {} : null;
  if (!source) return null;
  return {
    project, track, ...source, trace_context: 'train_rollout', training_run: run, training_batch: batch.id,
    training_sample_index: String(index), training_group: group.id, training_sample: sample.id == null || sample.id === '' ? String(index) : String(sample.id),
    return_experiment: experiment, return_run: run, return_view: 'training', return_panel: panel,
    return_cp: checkpoint, return_step: batch.step, return_baseline: baseline,
    return_selections: selections && Object.keys(selections).length ? JSON.stringify(selections) : '',
  };
}
