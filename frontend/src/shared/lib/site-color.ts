/**
 * 站点标识色：把站点名/id 哈希到 6 个 `--site-N` token（定义于 src/index.css）之一，
 * 保证同一个字符串永远落到同一个颜色桶——不需要维护"站点 id → 颜色"的映射表，
 * 新增 new-api 站点（"加站点零改代码"，见 frontend-contract.md）天然就有颜色，
 * 不用每加一个站点就回来改这个文件。
 *
 * 颜色桶数量必须和 index.css 里 `--site-0`..`--site-5` 的定义个数一致；
 * 那边加减颜色时要同步改这里的 SITE_COLOR_COUNT 和三个 CLASSES 数组。
 *
 * 重要：Tailwind v4 的类名扫描是纯文本匹配源码字符，不会执行 JS，所以不能用模板字符串
 * 拼 `` `bg-site-${i}` `` 这种运行时才拼出来的类名——扫描器找不到完整字面量就不会为它生成
 * CSS，样式会静默失效。三个导出函数都必须通过下面的字面量数组按下标查表，不能拼字符串。
 */

const SITE_COLOR_COUNT = 6;

const TEXT_CLASSES = ["text-site-0", "text-site-1", "text-site-2", "text-site-3", "text-site-4", "text-site-5"] as const;

const BG_CLASSES = ["bg-site-0", "bg-site-1", "bg-site-2", "bg-site-3", "bg-site-4", "bg-site-5"] as const;

/** 圆点 marker 是自包含的一整块 className（含尺寸与形状），账号行 / 图例前的 ● 标记直接套用即可。 */
const DOT_CLASSES = [
  "inline-block size-2 rounded-full bg-site-0",
  "inline-block size-2 rounded-full bg-site-1",
  "inline-block size-2 rounded-full bg-site-2",
  "inline-block size-2 rounded-full bg-site-3",
  "inline-block size-2 rounded-full bg-site-4",
  "inline-block size-2 rounded-full bg-site-5",
] as const;

/** 简单字符串哈希（djb2 变体），只用来取模分桶，不追求密码学强度。 */
function hashSiteName(name: string): number {
  let hash = 5381;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 33) ^ name.charCodeAt(i);
  }
  return Math.abs(hash);
}

function siteColorIndex(name: string): number {
  return hashSiteName(name) % SITE_COLOR_COUNT;
}

/** 站点名 → 文字颜色 class，如 `"text-site-2"`。用于站点标签文字、图例文字。 */
export function siteColorClass(name: string): string {
  return TEXT_CLASSES[siteColorIndex(name)];
}

/** 站点名 → 背景颜色 class，如 `"bg-site-2"`。用于站点色块、图表系列色。 */
export function siteBgClass(name: string): string {
  return BG_CLASSES[siteColorIndex(name)];
}

/** 站点名 → 可直接使用的圆点 marker className，如账号行前的站点色标签。 */
export function siteDotClass(name: string): string {
  return DOT_CLASSES[siteColorIndex(name)];
}
