import React, { useState } from 'react';
import { LayoutChangeEvent, StyleSheet, Text, View } from 'react-native';
import Svg, { Rect, Line } from 'react-native-svg';
import { C } from '../theme';

export type BarItem = {
  key: string;
  label: string;
  value: number;
  color?: string;
  sub?: string;
};

/** Horizontal bar ranking — good for pool / profit / reward-rate lists. */
export function HBarChart({
  items, maxValue, height = 18, empty = 'No data yet',
}: {
  items: BarItem[];
  maxValue?: number;
  height?: number;
  empty?: string;
}) {
  const [width, setWidth] = useState(0);
  const onLayout = (e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width);
  if (!items.length) {
    return <Text style={s.empty}>{empty}</Text>;
  }
  const peak = maxValue ?? Math.max(...items.map((i) => i.value), 1e-9);
  return (
    <View style={{ gap: 10 }} onLayout={onLayout}>
      {items.map((it) => {
        const frac = Math.max(0, Math.min(1, it.value / peak));
        const fillW = width > 0 ? Math.max(frac * width, frac > 0 ? 4 : 0) : 0;
        return (
          <View key={it.key}>
            <View style={s.barHead}>
              <Text style={s.barLabel} numberOfLines={1}>{it.label}</Text>
              <Text style={s.barValue}>{it.sub ?? String(it.value)}</Text>
            </View>
            <View style={[s.track, { height }]}>
              {width > 0 ? (
                <Svg width={width} height={height}>
                  <Rect x={0} y={0} width={width} height={height} fill={C.border} rx={4} />
                  <Rect x={0} y={0} width={fillW} height={height}
                        fill={it.color ?? C.blue} rx={4} />
                </Svg>
              ) : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

/** Tiny vertical spark bars (relative). */
export function SparkBars({
  values, color = C.blue, width = 120, height = 36,
}: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (!values.length) return null;
  const peak = Math.max(...values.map((v) => Math.abs(v)), 1e-9);
  const gap = 2;
  const bw = Math.max(2, (width - gap * (values.length - 1)) / values.length);
  return (
    <Svg width={width} height={height}>
      <Line x1={0} y1={height / 2} x2={width} y2={height / 2}
            stroke={C.border} strokeWidth={1} />
      {values.map((v, i) => {
        const h = (Math.abs(v) / peak) * (height * 0.45);
        const x = i * (bw + gap);
        const y = v >= 0 ? height / 2 - h : height / 2;
        return (
          <Rect key={i} x={x} y={y} width={bw} height={Math.max(h, 1)}
                fill={v >= 0 ? color : C.red} rx={1} />
        );
      })}
    </Svg>
  );
}

const s = StyleSheet.create({
  empty: { color: C.dim, fontSize: 12, paddingVertical: 6 },
  barHead: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4, gap: 8 },
  barLabel: { color: C.text, fontSize: 12, fontWeight: '600', flex: 1 },
  barValue: { color: C.sub, fontSize: 12, fontWeight: '700' },
  track: { width: '100%', borderRadius: 4, overflow: 'hidden', backgroundColor: C.border },
});
