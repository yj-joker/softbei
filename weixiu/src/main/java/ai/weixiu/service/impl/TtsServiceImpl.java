package ai.weixiu.service.impl;

import ai.weixiu.service.TtsService;
import com.alibaba.dashscope.audio.ttsv2.SpeechSynthesisAudioFormat;
import com.alibaba.dashscope.audio.ttsv2.SpeechSynthesisParam;
import com.alibaba.dashscope.audio.ttsv2.SpeechSynthesizer;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.nio.ByteBuffer;

/**
 * CosyVoice 语音合成实现：把文字同步合成为 mp3 音频字节。
 *
 * <p>和 ASR 一样直连 DashScope，{@code apikey} 仅驻留服务端，浏览器不接触。
 * SpeechSynthesizer 构造第二参为回调（异步流式用），传 null = 同步模式，
 * {@code call(text)} 直接返回完整 mp3 的 ByteBuffer。</p>
 *
 * <p>⚠ 注意：以下 DashScope SDK 调用（SpeechSynthesizer / SpeechSynthesisParam /
 *    SpeechSynthesisAudioFormat，包 {@code com.alibaba.dashscope.audio.ttsv2.*}）依赖
 *    dashscope-sdk-java 版本，首次构建若编译报错，按所装 SDK(2.18.0) 实际方法名/签名微调，
 *    与 AsrStreamHandler 的提示一致。</p>
 */
@Slf4j
@Service
public class TtsServiceImpl implements TtsService {

    /** 默认音色：龙小淳（知性女声）。 */
    private static final String DEFAULT_VOICE = "longxiaochun";
    /** 合成模型。 */
    private static final String MODEL = "cosyvoice-v1";
    /** 单次合成文本字符上限，超出截断，避免超长回复触发 SDK 上限/过度计费（风险点#2/#4）。 */
    private static final int MAX_TEXT_LEN = 2000;

    @Value("${apikey}")
    private String dashScopeApiKey;

    @Override
    public byte[] synthesize(String text, String voice) {
        if (text == null || text.trim().isEmpty()) {
            throw new IllegalArgumentException("待合成文本不能为空");
        }
        String safeText = text.length() > MAX_TEXT_LEN ? text.substring(0, MAX_TEXT_LEN) : text;
        String useVoice = (voice == null || voice.trim().isEmpty()) ? DEFAULT_VOICE : voice.trim();

        SpeechSynthesisParam param = SpeechSynthesisParam.builder()
                .model(MODEL)
                .voice(useVoice)
                .format(SpeechSynthesisAudioFormat.MP3_22050HZ_MONO_256KBPS)
                .apiKey(dashScopeApiKey)
                .build();

        // 第二参 null = 同步模式；call 返回完整 mp3 音频
        SpeechSynthesizer synthesizer = new SpeechSynthesizer(param, null);
        try {
            ByteBuffer audio = synthesizer.call(safeText);
            if (audio == null || audio.remaining() == 0) {
                throw new IllegalStateException("合成返回空音频");
            }
            byte[] bytes = new byte[audio.remaining()];
            audio.get(bytes);
            log.info("[TTS] 合成完成 voice={} 文本长度={} 音频字节={}", useVoice, safeText.length(), bytes.length);
            return bytes;
        } catch (Exception e) {
            log.warn("[TTS] 合成失败 voice={}: {}", useVoice, e.getMessage());
            throw new RuntimeException("语音合成失败: " + e.getMessage(), e);
        }
    }
}
