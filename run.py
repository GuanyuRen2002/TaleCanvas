#!/usr/bin/env python3
"""
TaleCanvas - 智能绘本生成器
启动脚本
"""

import os
import sys
from app import app, Config

def check_dependencies():
    """检查必要的依赖和配置"""
    missing_deps = []
    
    try:
        import openai
        import flask
        import PIL
        import reportlab
    except ImportError as e:
        missing_deps.append(str(e))
    
    if missing_deps:
        print("❌ 缺少必要依赖:")
        for dep in missing_deps:
            print(f"  - {dep}")
        print("\n请运行: pip install -r requirements.txt")
        return False
    
    # 检查API密钥
    gemini_key = app.config.get('GEMINI_API_KEY')
    openai_key = app.config['OPENAI_API_KEY']
    
    if not gemini_key or gemini_key == 'your-gemini-api-key-here':
        print("⚠️  警告: 请在config.py中设置您的Gemini API密钥")
        print("   或设置环境变量 GEMINI_API_KEY")
        print("   🎨 图片生成和📝 故事生成都需要Gemini API")
    else:
        print("✅ Gemini API密钥已配置 - 支持故事生成和图片生成")
    
    if openai_key == 'your-openai-api-key-here':
        print("💡 提示: OpenAI API密钥未配置（可选）")
        print("   当前使用Gemini API作为主要文本生成服务")
    
    return True

def main():
    """主函数"""
    print("🎨 TaleCanvas - 智能绘本生成器")
    print("=" * 50)
    
    if not check_dependencies():
        sys.exit(1)
    
    print(f"✅ 服务器启动中...")
    print(f"📍 地址: http://{app.config['HOST']}:{app.config['PORT']}")
    print(f"🔧 调试模式: {'开启' if app.config['DEBUG'] else '关闭'}")
    print("=" * 50)
    
    try:
        app.run(
            debug=app.config['DEBUG'], 
            host=app.config['HOST'], 
            port=app.config['PORT']
        )
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()