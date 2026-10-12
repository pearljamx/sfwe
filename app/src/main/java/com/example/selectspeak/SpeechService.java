package com.example.selectspeak;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.widget.Toast;
import java.util.Locale;

public final class SpeechService extends Service {
    private TextToSpeech engine;
    private boolean ready = false;
    private boolean initialized = false;
    private String pending;
    private int latestStartId;
    private final Handler main = new Handler(Looper.getMainLooper());
    @Override public void onCreate() {
        super.onCreate();
        engine = new TextToSpeech(getApplicationContext(), code ->
            main.post(() -> {
                initialized = true;
                ready = code == TextToSpeech.SUCCESS && engine != null;
                if (!ready) { report("请先安装英语语音引擎"); stopSelf(); return; }
                engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) {}
                    @Override public void onDone(String id) { main.post(() -> stopSelf(latestStartId)); }
                    @Override public void onError(String id) { main.post(() -> { report("朗读失败"); stopSelf(latestStartId); }); }
                });
                play();
            }));
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(startId); return START_NOT_STICKY; }
        pending = intent.getStringExtra("word");
        latestStartId = startId;
        if (pending == null || pending.isEmpty()) { stopSelf(startId); }
        else if (initialized) play();
        return START_NOT_STICKY;
    }
    private void play() {
        if (!ready || pending == null || engine == null) return;
        SharedPreferences prefs = getSharedPreferences("speak", MODE_PRIVATE);
        int languageResult = engine.setLanguage(Locale.forLanguageTag(prefs.getString("locale", "en-US")));
        if (languageResult == TextToSpeech.LANG_MISSING_DATA ||
                languageResult == TextToSpeech.LANG_NOT_SUPPORTED) languageResult = engine.setLanguage(Locale.US);
        if (languageResult == TextToSpeech.LANG_MISSING_DATA ||
                languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            report("未安装可用的英语语音包"); stopSelf(latestStartId); return;
        }
        engine.setSpeechRate(prefs.getFloat("rate", 0.9f));
        String text = pending; pending = null;
        if (engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "word_" + System.nanoTime()) == TextToSpeech.ERROR) {
            report("语音播放失败"); stopSelf(latestStartId);
        }
    }
    private void report(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }
    @Override public void onDestroy() {
        if (engine != null) { engine.stop(); engine.shutdown(); engine = null; }
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent i) { return null; }
}
