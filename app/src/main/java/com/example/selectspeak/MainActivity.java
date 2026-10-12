package com.example.selectspeak;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.SeekBar;
import android.widget.TextView;

public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        SharedPreferences prefs = getSharedPreferences("speak", MODE_PRIVATE);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(28, 32, 28, 28);
        setContentView(root);
        TextView intro = new TextView(this);
        intro.setText("选词朗读\n\n长按选中英文单词 → 更多 → 朗读。若 ChatGPT 没有显示第三方选词操作，可先在浏览器或笔记中测试。");
        intro.setTextSize(19);
        root.addView(intro);
        RadioGroup group = new RadioGroup(this);
        RadioButton us = new RadioButton(this);
        us.setText("美式英语"); us.setId(101);
        RadioButton uk = new RadioButton(this);
        uk.setText("英式英语"); uk.setId(102);
        group.addView(us); group.addView(uk); root.addView(group);
        group.check("en-GB".equals(prefs.getString("locale", "en-US")) ? 102 : 101);
        group.setOnCheckedChangeListener((g, id) ->
            prefs.edit().putString("locale", id == 102 ? "en-GB" : "en-US").apply());
        TextView speed = new TextView(this); speed.setTextSize(16);
        root.addView(speed);
        SeekBar bar = new SeekBar(this); bar.setMax(100);
        int start = Math.round((prefs.getFloat("rate", 0.9f) - 0.5f) * 100);
        bar.setProgress(Math.max(0, Math.min(100, start)));
        speed.setText("语速：" + (0.5f + bar.getProgress()/100f) + "x");
        bar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar b, int v, boolean fromUser) {
                float value = 0.5f + v/100f;
                speed.setText(String.format(java.util.Locale.US, "语速：%.2fx", value));
                if (fromUser) prefs.edit().putFloat("rate", value).apply();
            }
            @Override public void onStartTrackingTouch(SeekBar b) {}
            @Override public void onStopTrackingTouch(SeekBar b) {}
        });
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button test = new Button(this); test.setText("测试发音：beautiful");
        root.addView(test);
        test.setOnClickListener(v -> {
            Intent i = new Intent(this, SpeechService.class);
            i.putExtra("word", "beautiful");
            startService(i);
        });
    }
}
