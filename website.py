from flask import Flask, render_template, redirect, request, url_for

app = Flask(__name__)

# So we're always gonna have users log in
@app.route('/')
def member():
    return render_template('about.html')
    #return render_template('login.html')

@app.route('/aboutchinese')
def aboutchinese():
    return render_template('aboutchinese.html')

@app.route('/about')
def disp():
    return render_template('about.html')

if __name__ == "__main__":
    app.run()
