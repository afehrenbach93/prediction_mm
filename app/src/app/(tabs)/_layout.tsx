import { Tabs } from 'expo-router';
import React from 'react';
import { Ionicons } from '@expo/vector-icons';
import { C } from '../../theme';

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: C.bg },
        headerTintColor: C.text,
        headerTitleStyle: { fontWeight: '700' },
        tabBarStyle: { backgroundColor: C.bg, borderTopColor: C.border },
        tabBarActiveTintColor: C.blue,
        tabBarInactiveTintColor: C.dim,
        sceneStyle: { backgroundColor: C.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Overview',
          tabBarIcon: ({ color, size }) => <Ionicons name="speedometer-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="recap"
        options={{
          title: 'Recap',
          tabBarIcon: ({ color, size }) => <Ionicons name="today-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="predictions"
        options={{
          title: 'Predictions',
          tabBarIcon: ({ color, size }) => <Ionicons name="list-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="calibration"
        options={{
          title: 'Calibration',
          tabBarIcon: ({ color, size }) => <Ionicons name="stats-chart-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          tabBarIcon: ({ color, size }) => <Ionicons name="settings-outline" color={color} size={size} />,
        }}
      />
    </Tabs>
  );
}
