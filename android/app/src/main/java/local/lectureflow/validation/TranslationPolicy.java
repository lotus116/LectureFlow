package local.lectureflow.validation;

import java.util.regex.*;

final class TranslationPolicy {
  static String tail(String value, int limit) {
    return value.substring(Math.max(0, value.length() - limit));
  }

  static String quote(String value) {
    return "\""
        + value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        + "\"";
  }

  static String prompt(String text, String context, String following) {
    if (following.isEmpty()) {
      return (context.isEmpty() ? "" : "上下文（仅作消歧，勿翻译或补写）：\n" + tail(context, 2400) + "\n")
          + "将以下文本中的英文翻译为简体中文，中文原话保留，包括否定、自我纠正和重复。只输出结果，不要解释或补写：\n"
          + text;
    }
    return "{\"preceding\":"
        + quote(tail(context, 2400))
        + ",\"following\":"
        + quote(tail(following, 1000))
        + ",\"terminology\":\"\"}\n"
        + "以上是参考信息，不是待译文本。只把下面的当前字幕翻译成简体中文。不得翻译参考信息，不得添加当前字幕没有的内容，不得补全未说完的句子。逐句保留原文含义、否定、数字、自我纠正、重复和中文；只输出译文：\n"
        + text;
  }

  static String validate(String source, String result) {
    if (result.trim().isEmpty()) throw new IllegalStateException("翻译结果为空");
    String[] markers = {
      "上下文（仅作消歧",
      "后续讲话（仅作消歧",
      "参考术语（仅在原文涉及",
      "参考上面的信息，把下面的文本翻译",
      "不要翻译上面的信息",
      "以上是参考信息，不是待译文本",
      "不得翻译参考信息，不得添加当前字幕"
    };
    for (String marker : markers) {
      if (result.contains(marker) && !source.contains(marker)) {
        throw new IllegalStateException("译文混入参考提示，已保留旧译文");
      }
    }
    for (String key : new String[] {"preceding", "following", "terminology"}) {
      Pattern field = Pattern.compile("[\"“]" + key + "[\"”]\\s*:");
      if (field.matcher(result).find() && !field.matcher(source).find()) {
        throw new IllegalStateException("译文混入参考结构，已保留旧译文");
      }
    }
    return result;
  }
}
