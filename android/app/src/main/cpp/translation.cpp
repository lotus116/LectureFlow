#include <jni.h>
#include <string>
#include <vector>
#include <stdexcept>
#include "llama.h"

struct Translator {
    llama_model *model = nullptr;
    llama_context *context = nullptr;
    ~Translator() { if (context) llama_free(context); if (model) llama_model_free(model); }
};
static std::string utf8(JNIEnv *env, jstring text) {
    // JNI modified UTF-8 differs for surrogate pairs; input prompts are converted via UTF-8 bytes in Java.
    const char *value = env->GetStringUTFChars(text, nullptr);
    std::string result(value);
    env->ReleaseStringUTFChars(text, value);
    return result;
}
static void fail(JNIEnv *env, const char *message) {
    env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), message);
}
extern "C" JNIEXPORT jlong JNICALL Java_local_lectureflow_validation_LocalModels_loadTranslation(JNIEnv *env, jclass, jstring path) {
    llama_backend_init();
    auto *translator = new Translator();
    auto params = llama_model_default_params();
    params.n_gpu_layers = 0;
    translator->model = llama_model_load_from_file(utf8(env, path).c_str(), params);
    if (!translator->model) { delete translator; fail(env, "翻译模型加载失败"); return 0; }
    auto cp = llama_context_default_params();
    cp.n_ctx = 2048;
    cp.n_batch = 256;
    cp.n_threads = 4;
    cp.n_threads_batch = 4;
    translator->context = llama_init_from_model(translator->model, cp);
    if (!translator->context) { delete translator; fail(env, "翻译运行环境初始化失败"); return 0; }
    return reinterpret_cast<jlong>(translator);
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_local_lectureflow_validation_LocalModels_translateNative(JNIEnv *env, jclass, jlong handle, jbyteArray promptBytes) {
    auto *t = reinterpret_cast<Translator *>(handle);
    if (!t) { fail(env, "翻译模型尚未准备"); return nullptr; }
    std::string prompt(env->GetArrayLength(promptBytes), '\0');
    env->GetByteArrayRegion(promptBytes, 0, prompt.size(), reinterpret_cast<jbyte *>(prompt.data()));
    llama_chat_message message {"user", prompt.c_str()};
    const char *tmpl = llama_model_chat_template(t->model, nullptr);
    int count = llama_chat_apply_template(tmpl, &message, 1, true, nullptr, 0);
    if (count <= 0) { fail(env, "翻译模板不受支持"); return nullptr; }
    std::vector<char> formatted(count + 1);
    llama_chat_apply_template(tmpl, &message, 1, true, formatted.data(), formatted.size());
    auto *vocab = llama_model_get_vocab(t->model);
    int needed = -llama_tokenize(vocab, formatted.data(), count, nullptr, 0, true, true);
    if (needed <= 0 || needed > 1536) { fail(env, "翻译上下文超出窗口，保留待办"); return nullptr; }
    std::vector<llama_token> tokens(needed);
    llama_tokenize(vocab, formatted.data(), count, tokens.data(), tokens.size(), true, true);
    llama_memory_clear(llama_get_memory(t->context), true);
    for (int offset = 0; offset < needed; offset += 256) {
        auto batch = llama_batch_get_one(tokens.data() + offset, std::min(256, needed - offset));
        if (llama_decode(t->context, batch)) { fail(env, "翻译推理失败"); return nullptr; }
    }
    auto *sampler = llama_sampler_init_greedy();
    std::string output;
    bool ended = false;
    for (int i = 0; i < 512; ++i) {
        auto token = llama_sampler_sample(sampler, t->context, -1);
        if (llama_vocab_is_eog(vocab, token)) { ended = true; break; }
        char piece[256];
        int length = llama_token_to_piece(vocab, token, piece, sizeof(piece), 0, true);
        if (length < 0) { llama_sampler_free(sampler); fail(env, "翻译字符解码失败"); return nullptr; }
        output.append(piece, length);
        auto batch = llama_batch_get_one(&token, 1);
        if (llama_decode(t->context, batch)) { llama_sampler_free(sampler); fail(env, "翻译推理失败"); return nullptr; }
    }
    llama_sampler_free(sampler);
    if (!ended) { fail(env, "译文达到长度上限，保留旧译文和未完成记录"); return nullptr; }
    auto result = env->NewByteArray(output.size());
    env->SetByteArrayRegion(result, 0, output.size(), reinterpret_cast<const jbyte *>(output.data()));
    return result;
}
extern "C" JNIEXPORT void JNICALL Java_local_lectureflow_validation_LocalModels_closeTranslation(JNIEnv *, jclass, jlong handle) { delete reinterpret_cast<Translator *>(handle); }
