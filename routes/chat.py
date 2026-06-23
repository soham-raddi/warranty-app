from flask import Blueprint, jsonify, request

from chatbot import ask_digital_twin
from database import clear_chat_messages, get_chat_conversations, get_chat_messages, save_chat_message, search_chat_messages

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/chat', methods=['POST'])
def chat():
    data = request.json or {}
    user_message = data.get('message')
    history = data.get('history', [])
    conversation_id = data.get('conversation_id', 'legacy')

    if user_message:
        save_chat_message('user', user_message, conversation_id=conversation_id)

    result = ask_digital_twin(user_message, history)

    if isinstance(result, dict):
        assistant_reply = result.get('reply', '')
        if assistant_reply:
            save_chat_message('assistant', assistant_reply, conversation_id=conversation_id)
        return jsonify({
            'reply': assistant_reply,
            'action': result.get('action')
        })

    assistant_reply = str(result)
    if assistant_reply:
        save_chat_message('assistant', assistant_reply, conversation_id=conversation_id)
    return jsonify({'reply': assistant_reply, 'action': None})


@chat_bp.route('/api/chat/history', methods=['GET'])
def chat_history():
    conversation_id = request.args.get('conversation_id', default='legacy', type=str)
    limit = request.args.get('limit', default=100, type=int)
    query = request.args.get('query', default='', type=str)

    if query and query.strip():
        messages = search_chat_messages(query=query, conversation_id=conversation_id, limit=limit)
    else:
        messages = get_chat_messages(conversation_id=conversation_id, limit=limit)

    return jsonify({'messages': messages})


@chat_bp.route('/api/chat/conversations', methods=['GET'])
def chat_conversations():
    limit = request.args.get('limit', default=100, type=int)
    query = request.args.get('query', default='', type=str)
    conversations = get_chat_conversations(query=query, limit=limit)
    return jsonify({'conversations': conversations})


@chat_bp.route('/api/chat/conversations/<conversation_id>/messages', methods=['GET'])
def chat_conversation_messages(conversation_id):
    limit = request.args.get('limit', default=100, type=int)
    messages = get_chat_messages(conversation_id=conversation_id, limit=limit)
    return jsonify({'messages': messages, 'conversation_id': conversation_id})


@chat_bp.route('/api/chat/history', methods=['DELETE'])
def clear_chat_history():
    clear_chat_messages()
    return jsonify({'message': 'Chat history cleared successfully'})