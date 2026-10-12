package com.example.selectspeak;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

public final class ProcessTextActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Intent incoming = getIntent();
        CharSequence selected = incoming == null ? null :
                incoming.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT);
        String word = selected == null ? "" : selected.toString().trim();
        if (word.isEmpty()) {
            Toast.makeText(this, "未获取到选中的单词", Toast.LENGTH_SHORT).show();
        } else {
            Intent i = new Intent(this, SpeechService.class);
            i.putExtra("word", word.length() > 200 ? word.substring(0, 200) : word);
            try { startService(i); }
            catch (Exception e) { Toast.makeText(this, "无法启动朗读", Toast.LENGTH_SHORT).show(); }
        }
        finish();
    }
}
