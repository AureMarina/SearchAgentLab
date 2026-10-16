import {experimentParams} from './chunk-D875F66C32166310.js';
import {showSupervisionSample,trainingFormatHelp} from './chunk-976112D62544066A.js';
import {node,button,link,href,badge,select,field,section,empty,card,pct,count,modal,closeModal,jsonModal,table,addRow,controls,profileNotice,scoreCell,groupOf,gt,kpi} from './chunk-F51FD76F14331921.js';
import {originLabel,mountSource} from './chunk-876E8262FD6CA08D.js';
import {chart} from './chunk-51A0AEA413031BE3.js';
import {renderPlan} from './chunk-BDC5CC4569701DBB.js';
import {isNumber,trainingNumber,metricChoices,summarizeGroup,recordedTokenFields,trainingTraceParams} from './chunk-DB3887FF11C2DF88.js';

/** A shared training view: saved checkpoint evaluations and raw learning signals remain distinct. */
export async function mountDynamics(host, ctx) {
  const {api, project, track, q, alive} = ctx;
  const cat = ctx.catalog || await api('studio/catalog', {project, track});
  if (!alive()) return;
  const chosen = q.get('run') ? cat.experiments.find(item => item.runs.some(run => run.id === q.get('run')))
    : q.get('experiment') ? cat.experiments.find(item => item.experiment.id === q.get('experiment')) : cat.experiments[0];
  const run = chosen?.runs.find(item => item.id === q.get('run')) || chosen?.runs.at(-1);
  const panels = ctx.session.panels || [];
  const panel = q.get('panel') || ((chosen?.latest_evaluation?.run_id||chosen?.latest_evaluation?.run?.id)===run?.id?chosen.latest_evaluation.panel_id:'') || '';
  if (!ctx.embedded) {
    const head = node('div', 's-heading'), title = node('div');
    title.append(node('div', 's-eyebrow', 'TRAINING / ' + (track === 'reproduction' ? 'LITERATURE REPRODUCTION' : 'RESEARCH')),
      node('h1', '', '训练评测'), node('p', 's-subtitle', '先看 Checkpoint 的固定题结果，再查看训练奖励、Loss 与实际采样。'));
    head.append(title, controls(button('↻ 刷新', ctx.refresh, 's-btn'), link('评测报告 ↗', href('reports', {project, track}), 's-btn')));
    host.append(head);
    const selector = node('div', 's-selector-row'), models = [...new Set((chosen?.runs || []).map(item => item.base_model))];
    selector.append(field('实验', select(cat.experiments.map(item => [item.experiment.id, item.experiment.name]), chosen?.experiment.id,
      id => { location.hash = href('training', {project, track, experiment: id}); }, '实验')));
    if (models.length > 1) selector.append(field('模型', select(models.map(model => [model, model]), run?.base_model, model => {
      const selected = chosen.runs.find(item => item.base_model === model);
      location.hash = href('training', {project, track, run: selected.id, panel});
    }, '底座模型')));
    selector.append(field('运行', select((chosen?.runs || []).filter(item => item.base_model === run?.base_model)
      .map(item => [item.id, (item.name || item.id) + ' · seed ' + (item.seed ?? '未记录') + originLabel(item)]), run?.id,
      id => { location.hash = href('training', {project, track, run: id, panel}); }, '训练运行')));
    if (panels.length) selector.append(field('固定题单', select(panels.map(item => [item.id, item.name + ' · ' + item.size + ' 题']), panel,
      id => { location.hash = href('training', {project, track, run: run?.id, experiment: chosen?.experiment.id, panel: id}); }, '固定题单')));
    host.append(selector);
    if (chosen) {
      const notice = profileNotice(chosen.profile);
      if (notice) host.append(notice);
      host.append(controls(badge(chosen.profile.track === 'reproduction' ? '仅复现记录' : '当前实验'),
        button('查看实验档案', () => renderPlan(chosen.profile, chosen.experiment), 's-link')));
    }
    if (run) await mountSource(host, ctx, run.id);
  }
  if (!run) {
    host.append(empty('尚未登记此实验的运行。导入真实训练日志后，即可查看训练信号与 Checkpoint 评测。'));
    return;
  }
  let selections = {};
  try { const value = JSON.parse(q.get('selections') || '{}'); if (value && typeof value === 'object' && !Array.isArray(value)) selections = value; } catch {}
  let baseline = q.get('baseline') === 'initial' ? 'initial' : 'previous';
  let data = null, summary = null, dataNotice = '', loadEpoch = 0, autoTimer = null;
  let cpIndex = 0, selectedStep = 0, filter = 'all', lossKey = '', rewardKey = '', extraKey = '', validationKey = '';

  async function fetchData() {
    const result = await Promise.allSettled([
      api('studio/training-summary', {run: run.id, panel}),
      panel ? api('studio/dynamics', {run: run.id, panel, selections: JSON.stringify(selections), baseline}) : Promise.resolve(null),
    ]);
    const nextSummary = result[0].status === 'fulfilled' ? result[0].value : null;
    const nextData = result[1].status === 'fulfilled' ? result[1].value : null;
    if (!nextSummary && !nextData) throw new Error(result.find(item => item.status === 'rejected')?.reason?.message || '暂时无法读取此运行。');
    return {summary: nextSummary, data: nextData,
      notice: panel && result[1].status === 'rejected' ? '固定题评测暂未读取：' + result[1].reason.message : ''};
  }
  const initial = await fetchData();
  if (!alive()) return;
  ({data, summary, notice: dataNotice} = initial);
  const stage=()=>summary?.training_stage?.value||'unknown';
  const columns = () => data?.matrix?.columns || (summary?.checkpoints || []).map(checkpoint => ({checkpoint, evaluation: null}));
  const metricRows = () => summary?.metrics?.rows || data?.overview?.metrics || [];
  const batches = () => summary?.batches || data?.batches || [];
  const currentPanel = () => data?.overview?.panel || panels.find(item => item.id === panel) || null;
  const currentCheckpoint = () => columns()[cpIndex]?.checkpoint;
  const metricDefinitions = () => ({...(data?.overview?.metric_definitions || {}), ...(summary?.run?.metric_definitions || {})});
  const initialIndex = columns().findIndex(column => column.checkpoint.id === q.get('cp'));
  cpIndex = initialIndex >= 0 ? initialIndex : Math.max(0, columns().length - 1);
  selectedStep = q.has('step') && isNumber(Number(q.get('step'))) ? Number(q.get('step'))
    : currentCheckpoint()?.step ?? Math.max(0, ...metricRows().map(row => row.step), ...batches().map(batch => batch.step));
  ctx.markDemo(!!run.demo);

  const strip = node('div', 's-small-context');
  strip.append(node('span', '', `${run.status || '状态未记录'} · ${run.algorithm || '算法未记录'} · seed ${run.seed ?? '未记录'}`),
    button('完整运行配置', () => jsonModal('当前运行与原始配置', summary?.run || data?.overview?.run || run), 's-link'));
  strip.prepend(badge(summary?.training_stage?.label||'阶段未登记','purple'));
  strip.append(button('接入格式',trainingFormatHelp,'s-link'));
  if(summary?.training_stage?.dataset_version)strip.append(node('span','s-muted','训练数据 · '+summary.training_stage.dataset_version));
  host.append(strip);
  const summaryBox = node('div', 'f-kpis'), contextBox = node('div', 'f-summary-row');
  host.append(summaryBox, contextBox);
  const auto = node('input');
  auto.type = 'checkbox';
  auto.onchange = () => { clearInterval(autoTimer); if (auto.checked) autoTimer = setInterval(() => load(), 10000); };
  host.append(section('固定题结果与训练信号', field('自动刷新（10 秒）', auto)));
  const charts = node('div', 's-chart-grid'), metricDetails = node('details', 's-fold'), metricDetailBody = node('div', 's-chart-grid');
  metricDetails.append(node('summary', '', '更多已记录指标'), metricDetailBody);
  const batchbar = node('div', 's-batch-bar');
  host.append(charts, metricDetails, batchbar);
  const cpTools = controls(node('span', 's-muted', '题目变化对照：'), select([
    ['previous', '上一个已评测 Checkpoint'], ['initial', '初始已评测 Checkpoint'],
  ], baseline, value => { baseline = value; load(); }, 'Checkpoint 对照方式'));
  host.append(section('Checkpoint · 已保存评测', cpTools));
  const cpBox = card();
  host.append(cpBox);

  function panelContext() {
    const saved = summary?.checkpoints?.flatMap(checkpoint => checkpoint.evaluations || []) || [];
    const selected = columns()[cpIndex]?.evaluation;
    const record = saved.find(ev => ev.id === selected?.id)?.panel || summary?.context?.panel || saved.find(ev => ev.panel_id === panel)?.panel;
    const metadata = currentPanel(), isolation = record?.isolation || metadata?.isolation;
    const eligible = record?.generalization_eligible === true;
    return {
      title: eligible ? record?.split === 'test' ? '独立测试' : '独立验证' : '固定题诊断',
      label: eligible ? '已登记验证来源与训练隔离' : isolation?.status === 'not_checked' || !isolation ? '未核对训练隔离' : '按已登记范围诊断',
      notice: record?.scope_note || record?.notice || isolation?.notice || (panel ? '固定题的来源与训练隔离以已登记记录为准。' : '尚未关联固定题单。'),
      record: record || metadata, eligible,
    };
  }
  function returnParams() {
    return {project, track, return_experiment: chosen.experiment.id, return_run: run.id, return_view: 'training',
      return_panel: panel, return_cp: currentCheckpoint()?.id, return_step: selectedStep, return_baseline: baseline,
      return_selections: Object.keys(selections).length ? JSON.stringify(selections) : ''};
  }
  function url() {
    const patch = {panel, cp: currentCheckpoint()?.id, baseline, selections: Object.keys(selections).length ? JSON.stringify(selections) : '', step: selectedStep};
    if (ctx.setViewState) ctx.setViewState(patch);
    else history.replaceState(null, '', href('training', {project, track, run: run.id, ...patch}));
  }
  async function load() {
    const token = ++loadEpoch, checkpointId = currentCheckpoint()?.id;
    try {
      const next = await fetchData();
      if (!alive() || token !== loadEpoch) return;
      ({data, summary, notice: dataNotice} = next);
      const index = columns().findIndex(column => column.checkpoint.id === checkpointId);
      cpIndex = index >= 0 ? index : Math.max(0, columns().length - 1);
      render();
    } catch (error) { if (alive() && token === loadEpoch) ctx.toast(error.message); }
  }
  function choose(index) {
    if (!columns()[index]) return;
    cpIndex = index; selectedStep = columns()[index].checkpoint.step; filter = 'all'; render();
  }
  function refIndex(index = cpIndex) {
    if (baseline === 'initial') return columns().findIndex(column => column.evaluation);
    for (let i = index - 1; i >= 0; i--) if (columns()[i].evaluation) return i;
    return -1;
  }
  function renderSummary() {
    const column = columns()[cpIndex], ev = column?.evaluation, change = data?.changes?.[cpIndex];
    const stats = ev?.stats, context = panelContext();
    const result = stats?.fully_scored && ev.status === 'completed' && stats.expected > 0
      ? pct(stats.answer_correct / stats.expected) : ev ? count(stats) : '未评测';
    const delta = change?.groups ? change.groups.rescued - change.groups.harmed : null;
    const changeValue = change?.compatible && change?.full && delta != null ? `${delta > 0 ? '+' : ''}${delta} 题`
      : change?.compatible ? '仅配对题' : change ? '条件不同' : '待对照';
    const changeNote = change?.compatible && change.groups ? `错→对 ${change.groups.rescued} · 对→错 ${change.groups.harmed}` : '同题变化按已登记评测条件比较';
    const batchSummary = summary?.batch_summary, batchCount = batchSummary?.batch_count ?? batches().length;
    const trainingRows = isNumber(summary?.classification?.training_metric_rows) ? summary.classification.training_metric_rows : metricRows().length;
    const sampleCount = batchSummary?.sample_count ?? batches().reduce((sum, batch) => sum + (batch.trajectory_count || 0), 0);
    const advantage = batchSummary?.advantage;
    const advantageNote = advantage?.recorded > 0 ? ` · 非零优势 ${advantage.nonzero}/${advantage.recorded} 已记录样本` : '';
    summaryBox.replaceChildren(kpi(context.title, result, ev ? `${column.checkpoint.name || column.checkpoint.id} · ${count(stats)}` : '等待本题单上的已保存评测'),
      kpi(baseline === 'initial' ? '相对评测起点' : '相对上次评测', changeValue, changeNote),
      kpi('训练信号', `${trainingRows} step`, `${batchCount} batches · ${sampleCount} ${stage()==='sft'?'监督样本':stage()==='rl'?'rollouts':'训练样本'}${stage()==='sft'?'':advantageNote}`));
    contextBox.replaceChildren(badge(panel ? context.label : '尚未关联固定题单', context.eligible ? 'good' : 'muted'),
      node('span', 's-muted', !panel ? '训练日志与采样记录可直接查看。' : context.eligible ? '独立评测结果与训练日志分别保存。' : '固定题分数用于诊断；泛化判断需要训练隔离证据。'));
    if (panel) contextBox.append(button('题单来源与隔离', () => jsonModal('固定题单的实际来源与隔离记录', context.record), 's-link'));
    if (dataNotice) contextBox.append(node('span', 's-muted', dataNotice));
  }
  function renderCharts() {
    const rows = metricRows(), choices = metricChoices(rows), context = panelContext();
    if (!choices.loss.includes(lossKey)) lossKey = choices.loss.find(key => /pg_loss/.test(key)) || choices.loss[0] || '';
    if (!choices.reward.includes(rewardKey)) rewardKey = choices.reward[0] || '';
    if (!choices.extra.includes(extraKey)) extraKey = choices.extra[0] || '';
    if (!choices.validation.includes(validationKey)) validationKey = choices.validation.find(key => /success|acc|em/i.test(key)) || choices.validation[0] || '';
    const maxStep = Math.max(1, ...rows.map(row => row.step), ...columns().map(column => column.checkpoint.step), ...batches().map(batch => batch.step));
    const metricChart = (title, key, options, change, color, foot) => {
      const result = chart({title, key, points: rows.map(row => ({step: row.step, value: row[key]})), selected: selectedStep, maxStep, color,
        onSelect: step => { selectedStep = step; render(); }, foot});
      if (options.length > 1) result.querySelector('.s-metric-key')?.replaceWith(select(options.map(item => [item, item]), key, change, title + ' · 已记录字段'));
      return result;
    };
    const completed = columns().filter(column => column.evaluation?.stats?.fully_scored && column.evaluation.status === 'completed');
    const anchor = columns()[cpIndex]?.evaluation || completed.at(-1)?.evaluation;
    const points = completed.filter(column => !anchor || column.evaluation.protocol_hash === anchor.protocol_hash
      && column.evaluation.method_state?.fingerprint === anchor.method_state?.fingerprint).map(column => ({step: column.checkpoint.step,
      value: column.evaluation.stats.answer_correct, label: count(column.evaluation.stats), cp: column.checkpoint.id}));
    const hiddenCount = completed.length - points.length;
    const fixed = chart({title: context.title + ' · 正确数', key: currentPanel()?.name || (panel || '尚未关联固定题单'), points,
      selected: selectedStep, maxStep, yMax: currentPanel()?.size || undefined, kind: 'dots', color: 'var(--chart-coral)',
      onSelect: (step, point) => choose(columns().findIndex(column => column.checkpoint.id === point.cp)),
      foot: !panel ? '训练日志与采样记录可继续查看' : hiddenCount ? `仅展示相同协议与外部状态；另有 ${hiddenCount} 条记录可在表中查看`
        : context.eligible ? '仅显示完整已评分记录；逐次评测单独保存' : '固定题诊断分数；当前记录不足以支持泛化结论'});
    if (!points.length) fixed.querySelector('.s-empty').textContent = panel ? '本题单尚无完整已评分的 Checkpoint 记录' : '尚未关联固定题单';
    const visibleCharts=panel?[fixed]:[];
    if(stage()!=='sft'&&choices.reward.length)visibleCharts.push(metricChart('训练 Reward', rewardKey, choices.reward, key => { rewardKey = key; render(); }, 'var(--chart-gold)', '实际优化奖励；与固定题得分分别记录'));
    if(choices.loss.length)visibleCharts.push(metricChart('训练 Loss', lossKey, choices.loss, key => { lossKey = key; render(); }, 'var(--chart-blue)', '保留原始数值与负值；单独的 Loss 不代表端到端提升'));
    if(stage()==='sft'){
      const accuracy=choices.extra.find(key=>/(token|action|decision).*acc/i.test(key));
      if(accuracy)visibleCharts.push(metricChart('SFT 已记录准确率',accuracy,[accuracy],()=>{},'var(--chart-mint)','按原字段展示，不代表自主闭环能力'));
    }
    charts.replaceChildren(...visibleCharts);
    metricDetails.hidden = !choices.extra.length && !choices.validation.length;
    metricDetailBody.replaceChildren();
    if (choices.validation.length) metricDetailBody.append(metricChart('训练日志中的验证指标', validationKey, choices.validation,
      key => { validationKey = key; render(); }, 'var(--chart-mint)', '仅按日志原名展示；数据来源与题单归属未自动补全'));
    if (choices.extra.length) {
      const extra = metricChart('其他已记录指标', extraKey, choices.extra, key => { extraKey = key; render(); }, 'var(--chart-blue)', '只展示当前运行已记录的字段');
      const definition = metricDefinitions()[extraKey];
      if (definition) extra.append(button('查看指标定义', () => jsonModal('指标定义 · ' + extraKey, definition), 's-link'));
      metricDetailBody.append(extra);
    }
  }
  function renderBatchbar() {
    batchbar.replaceChildren();
    if(!batches().length){batchbar.append(node('div','cl-training-empty',metricRows().length?'已接入训练指标；逐样本学习证据未接入。':'尚未接入训练指标与逐样本学习证据。'),button('查看接入格式',trainingFormatHelp,'s-link'));return;}
    const steps = [...new Set([...batches().map(batch => batch.step), selectedStep])].filter(isNumber).sort((a, b) => a - b);
    const matching = batches().filter(batch => batch.step === selectedStep);
    batchbar.append(node('strong', '', stage()==='sft'?'监督样本':'训练样本'), field('优化 step', select(steps.map(step => [String(step), String(step)]), String(selectedStep), value => {
      selectedStep = Number(value); render();
    }, '训练 Batch step')), node('span', 's-muted', matching.length ? `${matching.length} 条 batch · ${matching.reduce((sum, batch) => sum + (batch.trajectory_count || 0), 0)} ${stage()==='sft'?'监督样本':'训练样本'}` : '该 step 尚无 batch 关联'));
    const open = button(stage()==='sft'?'查看监督样本 ↗':'查看 batch 与 rollout group ↗', showBatch, 's-link');
    open.disabled = !matching.length;
    batchbar.append(open);
  }
  function renderCheckpoints() {
    const t = table(['Checkpoint', '训练 step', stage()==='sft'?'训练 Loss':'训练 Reward', '固定题结果', '题目变化', '评分覆盖', '评测记录', '操作']);
    for (const [index, column] of columns().entries()) {
      const checkpoint = column.checkpoint, ev = column.evaluation, change = data?.changes?.[index];
      const reward = metricRows().find(row => row.step === checkpoint.step)?.[stage()==='sft'?lossKey:rewardKey], changes = node('div');
      if (change?.compatible && change.groups) {
        changes.append(badge('↑ ' + change.groups.rescued + ' 题', 'good'), badge('↓ ' + change.groups.harmed + ' 题', 'bad'));
        if (!change.full) changes.append(node('small', 's-muted', '仅已评分交集'));
      } else {
        changes.append(node('span', 's-muted', change ? '条件不同' : '—'));
        if (change) changes.append(button('查看条件', () => jsonModal('评测比较条件', change.reasons || []), 's-link'));
      }
      const recorded = checkpoint.evaluations || data?.overview?.checkpoints?.find(item => item.id === checkpoint.id)?.evaluations || [];
      let records = node('span', 's-muted', '未评测');
      const ops = controls(button('选中', () => choose(index), 's-link'));
      if (data && recorded.length) records = select(recorded.map(item => Array.isArray(item) ? item : [item.id, item.id]), ev?.id, id => {
        selections[checkpoint.id] = id; load();
      }, '评测记录 ' + checkpoint.id);
      else if (recorded.length) {
        records = node('div');
        for (const item of recorded) if (item.id) records.append(link(item.id, href('reports', {project, track, b: item.id}), 's-link'));
      }
      if (ev){
        const target=experimentParams(ctx,{project,track,experiment:chosen.experiment.id,run:run.id,panel,view:'results',result_b:ev.id,result_a:change?.reference_id||'',result_filter:'',trace_sample:'',trace_dataset:'',result_q:'',result_dataset:'',result_page:''});
        ops.append(link('查看逐题结果 →',href('experiment',target),'s-link'));
        if(change?.reference_id)ops.append(link('同题对比',href('experiment',{...target,view:'trace-compare'}),'s-link'));
      }
      const tr = addRow(t.body, [node('strong', '', checkpoint.name || checkpoint.id), checkpoint.step,
        trainingNumber(reward), node('strong', 's-score', count(ev?.stats)), changes,
        ev?.stats ? `${ev.stats.scored}/${ev.stats.expected} 已评分` : '—', records, ops], index === cpIndex ? 'selected' : '');
      tr.dataset.cp = checkpoint.id; tr.tabIndex = 0;
      tr.addEventListener('click', event => { if (!event.target.closest('button,a,select')) choose(index); });
      tr.onkeydown = event => { if (event.key === 'Enter' && event.target === tr) choose(index); };
    }
    cpTools.hidden = !data || !columns().length;
    cpBox.replaceChildren(t.wrap);
    if (!columns().length) cpBox.replaceChildren(empty('尚未登记 Checkpoint。现有训练日志与采样记录已保留。'));
  }
  async function showBatch() {
    const step = selectedStep;
    try {
      const result = await api('studio/batches', {run: run.id, step});
      if (!alive()) return;
      const body = modal(`${stage()==='sft'?'SFT 监督样本':'训练样本'} · step ${step}`);
      body.append(node('p', 's-muted', stage()==='sft'?'查看实际输入、示范、监督目标与 mask；不从文本重建缺失字段。':'查看本次优化采样实际收到的奖励、优势与 mask。缺失字段保留为未记录。'));
      if (!result.batches.length) { body.append(empty(result.notice)); return; }
      for (const batch of result.batches) {
        body.append(section(batch.id, button('完整 batch 记录', () => jsonModal('训练 batch 原始记录', batch), 's-link')));
        const sft=batch.training_stage==='sft'||(!batch.training_stage&&stage()==='sft');
        if(batch.dataset_version)body.append(node('p','s-muted','训练数据版本 · '+batch.dataset_version));
        for (const group of batch.groups) {
          if(sft){
            const box=node('details','s-fold');box.append(node('summary','',`${group.id} · ${group.samples.length} 监督样本`));
            const tableView=table(['样本','Loss','实际 Token / mask','监督记录']);
            for(const [index,sample] of group.samples.entries())addRow(tableView.body,[sample.id??'sample '+index,trainingNumber(sample.loss),recordedTokenFields(sample),button('查看监督样本',()=>showSupervisionSample(sample,batch,showBatch),'s-link')]);
            box.append(tableView.wrap);body.append(box);continue;
          }
          const stats = summarizeGroup(group.samples), box = node('details', 's-fold');
          box.append(node('summary', '', `${group.id} · ${group.samples.length} rollouts`));
          const groupSummary = controls(badge(`Reward 已记录 ${stats.reward.recorded}/${stats.sample_count}`), badge(`Advantage 已记录 ${stats.advantage.recorded}/${stats.sample_count}`));
          if (stats.reward.variation !== 'insufficient') groupSummary.append(badge(stats.reward.variation === 'mixed'
            ? `已记录奖励范围 ${trainingNumber(stats.reward.min)}–${trainingNumber(stats.reward.max)}` : `${stats.reward.complete ? '同组' : '已记录'}奖励一致`));
          if (stats.advantage.recorded) groupSummary.append(badge(`非零优势 ${stats.advantage.nonzero}/${stats.advantage.recorded}`));
          box.append(groupSummary);
          if (typeof group.question === 'string') box.append(node('p', 's-muted', group.question));
          const t = table(['样本', 'Reward', 'Advantage', 'Loss', '实际 Token / mask', '轨迹']);
          for (const [index, sample] of group.samples.entries()) {
            const params = trainingTraceParams({project, track, experiment: chosen.experiment.id, run: run.id, panel,
              checkpoint: currentCheckpoint()?.id, baseline, selections, batch, group, sample, index});
            const trace = params ? link('查看训练轨迹 ↗', href('replay', params)) : node('span', 's-muted', '未关联轨迹');
            if (params) trace.addEventListener('click', closeModal);
            addRow(t.body, [sample.id == null || sample.id === '' ? 'sample ' + index : sample.id,
              trainingNumber(sample.reward), trainingNumber(sample.advantage), trainingNumber(sample.loss),
              button(recordedTokenFields(sample), () => jsonModal('实际训练字段 · ' + (sample.id ?? index), sample), 's-link'), trace]);
          }
          box.append(t.wrap);
          if (stats.reward.recorded < stats.sample_count || stats.advantage.recorded < stats.sample_count)
            box.append(node('p', 's-muted', '覆盖统计仅计入已记录字段，未从最终成败推算奖励或优势。'));
          body.append(box);
        }
      }
    } catch (error) { if (alive()) ctx.toast(error.message); }
  }
  function render() { url(); renderSummary(); renderCharts(); renderBatchbar(); renderCheckpoints(); }
  render();
  return () => { clearInterval(autoTimer); loadEpoch++; };
}
