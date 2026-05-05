// Shared row-sorting helper: numeric compare when both sides parse as
// finite non-empty numbers, otherwise locale string compare with
// numeric option.  NULLs always sort last.

export type Cell = string | number | boolean | null

export function sortRowsByColumn(
  rows: Cell[][],
  colIdx: number,
  dir: 'asc' | 'desc',
): Cell[][] {
  const sign = dir === 'asc' ? 1 : -1
  rows.sort((a, b) => {
    const av = a[colIdx]
    const bv = b[colIdx]
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    const an = typeof av === 'number' ? av : Number(av)
    const bn = typeof bv === 'number' ? bv : Number(bv)
    if (
      Number.isFinite(an) && Number.isFinite(bn)
      && String(av).trim() !== '' && String(bv).trim() !== ''
    ) {
      return (an - bn) * sign
    }
    return String(av).localeCompare(String(bv), undefined, { numeric: true }) * sign
  })
  return rows
}
