# Use an official Python runtime as the base image 
FROM python:3.10-slim 
 
# Install FFmpeg and other dependencies 
 
# Set the working directory in the container 
WORKDIR /app 
 
# Copy the project files into the container 
COPY . . 
 
# Install Python dependencies 
RUN pip install --no-cache-dir -r requirements.txt 
 
# Expose the port your app runs on (Fly.io will override this if needed) 
EXPOSE 8080 
 
# Command to run your app using Gunicorn (as specified in your Procfile) 
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080"] 
