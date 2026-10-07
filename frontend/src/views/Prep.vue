<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const tree = ref<any[]>([])
const data = ref<any>(null)
const shortages = ref<any[]>([])
const orders = ref<any[]>([])

// 备料台永远展示“正在用的那张单”（每订单最新一张已落快照），
// 打开页面只读单、不现算、不偷偷落新单；点“生成备料单”才落一张新单。
async function loadLatest() {
  const res = await api('/prep/latest?order_id=1')
  data.value = res
  shortages.value = res.shortages || []
}

async function run() {
  // 生成 = 按当前倍数栏取整落一张新单；占用列与缺料贴取自同一响应，天然一套数。
  data.value = await api('/prep/run?order_id=1', { method: 'POST' })
  shortages.value = data.value.shortages || []
}

onMounted(async () => {
  tree.value = await api('/bom/tree')
  orders.value = await api('/orders')
  await loadLatest()
})
</script>
<template>
  <h1>备料工作台</h1>
  <p class="sub">左 BOM 树 · 中备料表 · 右缺料便利贴 · 顶栏订单芯片</p>
  <div class="kp-chips" style="margin-bottom:0.75rem" v-if="orders.length">
    <span v-for="o in orders" :key="o.id" class="kp-chip" style="cursor:default">
      {{ o.code }} · {{ o.outlet }}
    </span>
  </div>
  <div style="display:flex;gap:0.5rem;align-items:center">
    <button class="btn" @click="run">生成备料单</button>
    <button class="btn" style="background:#eee" @click="loadLatest">读正在用的单</button>
  </div>
  <div class="kp-workbench" style="margin-top:0.85rem">
    <aside class="kp-bom-tree">
      <h2>菜品 / BOM</h2>
      <div v-for="d in tree" :key="d.code" class="kp-dish-node">
        <strong>{{ d.dish }}</strong>
        <span style="font-size:0.7rem;color:#8a8078">{{ d.code }}</span>
        <ul>
          <li v-for="(c,i) in d.children" :key="i">{{ c.ingredient }} · {{ c.qty }} {{ c.unit }}</li>
        </ul>
      </div>
    </aside>
    <section class="kp-worksheet" v-if="data">
      <h2>备料单 #{{ data.id }} · {{ data.order?.code }} · {{ data.order?.outlet }}</h2>
      <table>
        <thead><tr><th>原料</th><th>散数需求</th><th>倍数</th><th>占用（取整）</th><th>库存</th><th>单位</th></tr></thead>
        <tbody>
          <tr v-for="l in data.prep_lines" :key="l.ingredient_id">
            <td>{{ l.ingredient_name }}</td>
            <td class="muted">{{ l.raw_need_qty }}</td>
            <td>{{ l.prep_multiple == null ? '—' : l.prep_multiple }}</td>
            <td><strong>{{ l.need_qty }}</strong></td>
            <td>{{ l.stock_qty }}</td>
            <td>{{ l.unit }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <aside class="kp-shortage-sticky">
      <h2>⚠ 缺料便利贴</h2>
      <div v-for="r in shortages" :key="r.ingredient_id" class="kp-shortage-item">
        <span>{{ r.ingredient_name }}</span>
        <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
      </div>
      <p v-if="!shortages.length" style="font-size:0.8rem;margin:0.5rem 0 0">暂无缺料</p>
    </aside>
  </div>
</template>
