import random
import string
import uuid
import io
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, join_room, emit
import qrcode

BASE_URL = "https://philippsseite.de"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'workshop-feedback-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory storage for sessions
sessions = {}


def generate_session_code():
    """Generate an 8-digit numeric code."""
    return ''.join(random.choices(string.digits, k=8))


def generate_qr_code(url):
    """Generate a QR code as base64 string."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode()


def get_local_ip():
    """Get the local IP address for network access."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


# Routes
@app.route('/')
def index():
    """Host landing page to create a session."""
    return render_template('host.html')


@app.route('/join', methods=['GET'])
def join_page():
    """Page to enter session code manually."""
    return render_template('join.html')


@app.route('/join/<code>')
def join_session(code):
    """Join a session by code."""
    if code not in sessions:
        return render_template('join.html', error="Session not found")
    return render_template('session.html', code=code)


@app.route('/host/<code>')
def host_session(code):
    """Host view of a session."""
    if code not in sessions:
        return "Session not found", 404
    return render_template('session.html', code=code, is_host=True)


@app.route('/api/create-session', methods=['POST'])
def create_session():
    """Create a new session and return code + QR."""
    code = generate_session_code()
    while code in sessions:  # Ensure unique code
        code = generate_session_code()
    
    sessions[code] = {
        'created_at': datetime.now().isoformat(),
        'feedbacks': []
    }
    
    local_ip = get_local_ip()
    join_url = f"{BASE_URL}/join/{code}"
    qr_base64 = generate_qr_code(join_url)
    
    return jsonify({
        'code': code,
        'join_url': join_url,
        'qr_code': qr_base64,
        'host_url': f"/host/{code}"
    })


@app.route('/api/session/<code>')
def get_session(code):
    """Get session data."""
    if code not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    return jsonify(sessions[code])


@app.route('/api/session/<code>/export')
def export_session(code):
    """Export session as markdown."""
    if code not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    
    session = sessions[code]
    
    # Sort feedbacks by votes (descending)
    sorted_feedbacks = sorted(session['feedbacks'], key=lambda x: x['votes'], reverse=True)
    
    md_lines = [
        f"# Workshop Feedback Session",
        f"",
        f"**Session Code:** {code}",
        f"**Created:** {session['created_at']}",
        f"**Exported:** {datetime.now().isoformat()}",
        f"",
        f"---",
        f"",
        f"## Feedback ({len(sorted_feedbacks)} items)",
        f""
    ]
    
    for i, fb in enumerate(sorted_feedbacks, 1):
        md_lines.append(f"### {i}. {fb['text']}")
        md_lines.append(f"**Votes:** {fb['votes']}")
        md_lines.append(f"")
        
        if fb['comments']:
            md_lines.append(f"**Comments:**")
            for comment in fb['comments']:
                md_lines.append(f"- {comment['text']} (↑{comment['votes']})")
            md_lines.append(f"")
    
    return jsonify({
        'markdown': '\n'.join(md_lines),
        'filename': f"feedback-{code}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    })


@app.route('/api/session/<code>/close', methods=['POST'])
def close_session(code):
    """Close and delete a session."""
    if code in sessions:
        del sessions[code]
    return jsonify({'success': True})


# Socket.IO Events
@socketio.on('join')
def on_join(data):
    """User joins a session room."""
    code = data['code']
    if code in sessions:
        join_room(code)
        emit('joined', {'code': code, 'feedbacks': sessions[code]['feedbacks']})
    else:
        emit('error', {'message': 'Session not found'})


@socketio.on('new_feedback')
def on_new_feedback(data):
    """Add new feedback to a session."""
    code = data['code']
    text = data['text'].strip()
    user_id = data.get('user_id', 'anonymous')
    
    if not text or code not in sessions:
        return
    
    feedback = {
        'id': str(uuid.uuid4()),
        'text': text,
        'votes': 0,
        'voters': [],
        'comments': [],
        'created_at': datetime.now().isoformat()
    }
    
    sessions[code]['feedbacks'].append(feedback)
    emit('feedback_added', feedback, room=code)


@socketio.on('new_comment')
def on_new_comment(data):
    """Add a comment to a feedback."""
    code = data['code']
    feedback_id = data['feedback_id']
    text = data['text'].strip()
    
    if not text or code not in sessions:
        return
    
    for fb in sessions[code]['feedbacks']:
        if fb['id'] == feedback_id:
            comment = {
                'id': str(uuid.uuid4()),
                'text': text,
                'votes': 0,
                'voters': [],
                'created_at': datetime.now().isoformat()
            }
            fb['comments'].append(comment)
            emit('comment_added', {'feedback_id': feedback_id, 'comment': comment}, room=code)
            break


@socketio.on('vote')
def on_vote(data):
    """Upvote a feedback or comment."""
    code = data['code']
    item_id = data['item_id']
    user_id = data.get('user_id', 'anonymous')
    is_comment = data.get('is_comment', False)
    feedback_id = data.get('feedback_id')  # Only for comments
    
    if code not in sessions:
        return
    
    for fb in sessions[code]['feedbacks']:
        if is_comment and fb['id'] == feedback_id:
            for comment in fb['comments']:
                if comment['id'] == item_id:
                    if user_id not in comment['voters']:
                        comment['voters'].append(user_id)
                        comment['votes'] += 1
                        emit('vote_updated', {
                            'item_id': item_id,
                            'votes': comment['votes'],
                            'is_comment': True,
                            'feedback_id': feedback_id
                        }, room=code)
                    break
            break
        elif not is_comment and fb['id'] == item_id:
            if user_id not in fb['voters']:
                fb['voters'].append(user_id)
                fb['votes'] += 1
                emit('vote_updated', {
                    'item_id': item_id,
                    'votes': fb['votes'],
                    'is_comment': False
                }, room=code)
            break


if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"Workshop Feedback Tool")
    print(f"{'='*50}")
    print(f"Local URL: http://localhost:5000")
    print(f"Network URL: http://{get_local_ip()}:5000")
    print(f"{'='*50}\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
